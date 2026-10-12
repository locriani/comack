//! [`ButtonInput`] over the ESP GPIO inputs, with falling-edge interrupts
//! latching presses that arrive while the viewer's loop is blocked.

use std::sync::Arc;
use std::time::Instant as StdInstant;

use embedded_hal::digital::{ErrorType, InputPin};
use esp_idf_svc::hal::gpio::{Input as EspInputMode, InterruptType, PinDriver};
use esp_idf_svc::sys::EspError;
use log::warn;

use comack_core::adapters::{ActiveLowButtons, PressLatch};
use comack_core::application::ports::ButtonInput;
use comack_core::domain::{ButtonLevels, ChordGuard, Instant};

/// One button pin: samples the level and re-arms the edge interrupt.
///
/// The HAL erases the pin model (`PinDriver<'d, MODE>` keeps only the mode),
/// and it disables a pin's interrupt after every ISR notification (to avoid
/// IWDT triggers), so re-arming rides the viewer's 50 ms sampling tick:
/// `is_low` runs in non-ISR context once per tick.
struct InterruptiblePin<'d> {
    pin: PinDriver<'d, EspInputMode>,
    latch: Arc<PressLatch>,
    index: usize,
}

impl<'d> InterruptiblePin<'d> {
    /// Arm the falling-edge interrupt; the ISR only latches the press.
    fn arm(&mut self) -> Result<(), EspError> {
        self.pin.set_interrupt_type(InterruptType::NegEdge)?;
        // Safety: the ISR callback only performs an atomic store, as the
        // HAL's ISR contract requires.
        let latch = Arc::clone(&self.latch);
        let index = self.index;
        unsafe {
            self.pin.subscribe(move || latch.mark_pressed(index))?;
        }
        // `subscribe` disables interrupts as a side effect; arm it now.
        self.pin.enable_interrupt()
    }
}

impl ErrorType for InterruptiblePin<'_> {
    type Error = core::convert::Infallible;
}

impl InputPin for InterruptiblePin<'_> {
    fn is_high(&mut self) -> Result<bool, Self::Error> {
        Ok(!self.is_low()?)
    }

    fn is_low(&mut self) -> Result<bool, Self::Error> {
        // An unreadable pin counts as released; the inherent `is_low` is
        // already infallible. The driver disabled the interrupt after its
        // last notification — re-arm from non-ISR context so the next edge
        // is caught.
        let pressed = self.pin.is_low();
        if let Err(e) = self.pin.enable_interrupt() {
            warn!("Button rearm: {e}");
        }
        Ok(pressed)
    }
}

/// The three front buttons: sampled per tick, with presses latched by
/// falling-edge interrupts so a press fully contained in a blocked
/// `show()`/`fetch()` window still yields one rising edge on the next tick.
pub struct InterruptButtons<'d> {
    buttons: ActiveLowButtons<InterruptiblePin<'d>, InterruptiblePin<'d>, InterruptiblePin<'d>>,
    latch: Arc<PressLatch>,
    /// Keeps the setup chord from also counting as ordinary presses.
    chord: ChordGuard,
    origin: StdInstant,
}

impl<'d> InterruptButtons<'d> {
    /// Wrap the three configured input pins and arm their interrupts.
    pub fn new(
        latch: Arc<PressLatch>,
        forward: PinDriver<'d, EspInputMode>,
        back: PinDriver<'d, EspInputMode>,
        trigger: PinDriver<'d, EspInputMode>,
    ) -> Result<Self, EspError> {
        let mut forward = InterruptiblePin {
            pin: forward,
            latch: Arc::clone(&latch),
            index: PressLatch::FORWARD,
        };
        let mut back = InterruptiblePin {
            pin: back,
            latch: Arc::clone(&latch),
            index: PressLatch::BACK,
        };
        let mut trigger = InterruptiblePin {
            pin: trigger,
            latch: Arc::clone(&latch),
            index: PressLatch::TRIGGER,
        };
        forward.arm()?;
        back.arm()?;
        trigger.arm()?;
        Ok(Self {
            buttons: ActiveLowButtons::new(forward, back, trigger),
            latch,
            chord: ChordGuard::new(),
            origin: StdInstant::now(),
        })
    }
}

impl ButtonInput for InterruptButtons<'_> {
    fn levels(&mut self) -> ButtonLevels {
        let raw = self.buttons.levels();
        let mut pressed = raw;
        self.latch.consume(&mut pressed);
        self.chord
            .filter(Instant::from_elapsed(self.origin.elapsed()), raw, pressed)
    }
}
