//! Colour comics on the Spectra 6 panel (reTerminal E1002).
//!
//! The viewer only knows 1-bit [`Frame`]s: it calls `ComicSource::fetch` and
//! then `FrameDisplay::show`. These two wrappers ride along without touching
//! it. [`ColorComics`] downloads the colour image into a shared slot and marks
//! it ready; [`ColorSwitch`] shows the slot's image instead of the 1-bit frame
//! when one is ready. Portal and status screens never fill the slot, so they
//! stay 1-bit.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use anyhow::Result;
use log::warn;

use comack_core::adapters::{ComicApiError, HttpComicService, TcpConnector};
use comack_core::application::ports::{ColorFrameDisplay, ComicSource, ComicTrigger, FrameDisplay};
use comack_core::domain::{ColorFrame, Frame, Issue, COLOR_FRAME_BYTES};

/// The colour image shared between the source and the display. Both live on
/// the main task, so no locking is needed.
pub struct ColorSlot {
    image: RefCell<Box<ColorFrame>>,
    ready: Cell<bool>,
}

impl ColorSlot {
    /// Allocate the 192 KB image (PSRAM, per `CONFIG_SPIRAM_USE_MALLOC`).
    pub fn new() -> Result<Rc<Self>> {
        let image = vec![0u8; COLOR_FRAME_BYTES]
            .into_boxed_slice()
            .try_into()
            .map_err(|_| anyhow::anyhow!("colour buffer size"))?;
        Ok(Rc::new(Self {
            image: RefCell::new(image),
            ready: Cell::new(false),
        }))
    }
}

/// A [`ComicSource`] that prefers the colour image, falling back to the
/// 1-bit frame when the server has none (an older server, or a failed
/// download).
pub struct ColorComics<'a, C> {
    inner: HttpComicService<'a, C>,
    slot: Rc<ColorSlot>,
}

impl<'a, C> ColorComics<'a, C> {
    pub fn new(inner: HttpComicService<'a, C>, slot: Rc<ColorSlot>) -> Self {
        Self { inner, slot }
    }
}

impl<C: TcpConnector> ComicSource for ColorComics<'_, C> {
    type Error = ComicApiError;

    fn latest_issue(&mut self) -> Result<Option<Issue>, ComicApiError> {
        self.inner.latest_issue()
    }

    fn fetch(&mut self, issue: Issue, frame: &mut Frame) -> Result<(), ComicApiError> {
        self.slot.ready.set(false);
        // The monochrome E1001 has no use for the 192 KB download.
        if cfg!(feature = "e1002") {
            match self
                .inner
                .fetch_color(issue, &mut self.slot.image.borrow_mut())
            {
                Ok(()) => {
                    self.slot.ready.set(true);
                    return Ok(());
                }
                Err(e) => warn!(
                    "Colour comic #{} unavailable ({e:?}); using black and white.",
                    issue.get()
                ),
            }
        }
        self.inner.fetch(issue, frame)
    }
}

impl<C: TcpConnector> ComicTrigger for ColorComics<'_, C> {
    type Error = ComicApiError;

    fn trigger(&mut self) -> Result<(), ComicApiError> {
        self.inner.trigger()
    }
}

/// A [`FrameDisplay`] that shows the slot's colour image when one is ready
/// (once), else the 1-bit frame it is handed.
pub struct ColorSwitch<D> {
    inner: D,
    slot: Rc<ColorSlot>,
}

impl<D> ColorSwitch<D> {
    pub fn new(inner: D, slot: Rc<ColorSlot>) -> Self {
        Self { inner, slot }
    }
}

impl<D: ColorFrameDisplay> FrameDisplay for ColorSwitch<D> {
    type Error = D::Error;

    fn show(&mut self, frame: &Frame) -> Result<(), Self::Error> {
        if self.slot.ready.replace(false) {
            self.inner.show_color(&self.slot.image.borrow())
        } else {
            self.inner.show(frame)
        }
    }
}
