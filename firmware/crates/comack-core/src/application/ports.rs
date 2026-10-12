//! Boundaries between the use cases and the outside world.
//!
//! Each port is deliberately small (Interface Segregation) and expressed in
//! domain terms, so use cases do not know about HTTP, WebSockets, SPI or
//! MicroLink (Dependency Inversion).

use core::fmt::Debug;

use crate::domain::{Button, ButtonLevels, ColorFrame, DeviceConfig, Frame, Instant, Issue};

/// Device configuration storage: load the settings the device runs with,
/// or `None` when nothing is stored yet (then the compile-time fallback
/// keeps the device booting).
///
/// Implemented by the ESP adapter over the default NVS partition
/// (`comack` namespace) and by an in-memory double for host tests.
pub trait DeviceConfigStore {
    /// Why a load, save or clear failed.
    type Error;

    /// Reads the stored configuration, or `None` when nothing is stored.
    fn load(&self) -> Result<Option<DeviceConfig>, Self::Error>;

    /// Persists the configuration, replacing whatever was stored.
    fn save(&mut self, config: &DeviceConfig) -> Result<(), Self::Error>;

    /// Erases the stored configuration; the device falls back to its
    /// compile-time values on the next boot.
    fn clear(&mut self) -> Result<(), Self::Error>;
}

/// A monotonic clock.
pub trait Clock {
    /// The current time.
    fn now(&self) -> Instant;
}

/// Somewhere a frame can be shown: the e-paper panel, a file, a test double.
pub trait FrameDisplay {
    /// Why showing failed.
    type Error: Debug;

    /// Show `frame` (see [`Frame`] for the pixel format).
    fn show(&mut self, frame: &Frame) -> Result<(), Self::Error>;
}

/// A display that can also show a [`ColorFrame`] (the Spectra 6 panel).
pub trait ColorFrameDisplay: FrameDisplay {
    /// Show `image` (see [`ColorFrame`] for the pixel format).
    fn show_color(&mut self, image: &ColorFrame) -> Result<(), Self::Error>;
}

/// The front-panel buttons.
pub trait ButtonInput {
    /// Sample which buttons are held down right now.
    fn levels(&mut self) -> ButtonLevels;
}

/// Read access to the published comics.
pub trait ComicSource {
    /// Why a lookup failed.
    type Error: Debug;

    /// The newest revealed issue, or `None` if nothing has been revealed yet.
    fn latest_issue(&mut self) -> Result<Option<Issue>, Self::Error>;

    /// Load `issue` into `frame`. On error the frame contents are unspecified.
    fn fetch(&mut self, issue: Issue, frame: &mut Frame) -> Result<(), Self::Error>;
}

/// Asks the server to generate and reveal a comic now, as if the trigger
/// phrase had been spoken.
pub trait ComicTrigger {
    /// Why the request failed.
    type Error: Debug;

    /// Fire the trigger.
    fn trigger(&mut self) -> Result<(), Self::Error>;
}

/// Reports button presses to the server, where the dashboard shows a chip.
pub trait PressReporter {
    /// Report one press. Fire-and-forget: the adapter logs failures and
    /// carries on, so a report never blocks navigation.
    fn report(&mut self, button: Button);
}

/// Switches the device's press-indication LED.
pub trait PressFeedback {
    /// Turn the press LED on or off. The LED is cosmetic: the adapter logs
    /// failures and carries on, so switching never blocks the viewer.
    fn set_led(&mut self, on: bool);
}

/// A source of 16-bit mono PCM audio.
pub trait AudioSource {
    /// Why reading failed.
    type Error: Debug;

    /// Fill `buffer` with up to `buffer.len()` samples, blocking until they
    /// are available. Returns how many samples were written.
    fn read(&mut self, buffer: &mut [i16]) -> Result<usize, Self::Error>;
}

/// Opens audio sessions to the server.
pub trait AudioUplink {
    /// An open session.
    type Session: AudioSession;
    /// Why opening failed.
    type Error: Debug;

    /// Open a new session.
    fn open(&mut self) -> Result<Self::Session, Self::Error>;
}

/// An open audio session. Dropping it closes the session.
pub trait AudioSession {
    /// Why sending failed. Any error means the session is dead.
    type Error: Debug;

    /// Deliver one chunk of PCM samples.
    fn send(&mut self, pcm: &[i16]) -> Result<(), Self::Error>;
}
