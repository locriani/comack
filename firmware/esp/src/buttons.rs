//! [`ButtonInput`] over the ESP GPIO inputs, with falling-edge interrupts
//! latching presses that arrive while the viewer's loop is blocked.

use std::sync::Arc;

use embedded_hal::digital::{ErrorType, InputPin};
use esp_idf_svc::hal::gpio::{Input as EspInputMode, InterruptType, PinDriver};
use esp_idf_svc::sys::EspError;
use log::warn;

use comack_core::adapters::{ActiveLowButtons, PressLatch};
use comack_core::application::ports::ButtonInput;
use comack_core::domain::ButtonLevels;

use crate::setup_button::SetupHold;

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
    /// Only the setup-designated pin carries this: every level sample
    /// feeds the hold detector, so a 3 s hold trips it wherever the
    /// viewer is in its loop.
    hold: Option<Arc<SetupHold>>,
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
        if let Some(hold) = &self.hold {
            hold.sample(pressed);
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
}

impl<'d> InterruptButtons<'d> {
    /// Wrap the three configured input pins and arm their interrupts.
    /// `setup_hold` watches the setup-designated pin (the back button, per
    /// [`crate::setup_button`]); every tick feeds it a level sample.
    pub fn new(
        latch: Arc<PressLatch>,
        forward: PinDriver<'d, EspInputMode>,
        back: PinDriver<'d, EspInputMode>,
        trigger: PinDriver<'d, EspInputMode>,
        setup_hold: Arc<SetupHold>,
    ) -> Result<Self, EspError> {
        let mut forward = InterruptiblePin {
            pin: forward,
            latch: Arc::clone(&latch),
            index: PressLatch::FORWARD,
            hold: None,
        };
        let mut back = InterruptiblePin {
            pin: back,
            latch: Arc::clone(&latch),
            index: PressLatch::BACK,
            hold: Some(setup_hold),
        };
        let mut trigger = InterruptiblePin {
            pin: trigger,
            latch: Arc::clone(&latch),
            index: PressLatch::TRIGGER,
            hold: None,
        };
        forward.arm()?;
        back.arm()?;
        trigger.arm()?;
        Ok(Self {
            buttons: ActiveLowButtons::new(forward, back, trigger),
            latch,
        })
    }
}

impl ButtonInput for InterruptButtons<'_> {
    fn levels(&mut self) -> ButtonLevels {
        let mut levels = self.buttons.levels();
        self.latch.consume(&mut levels);
        levels
    }
}
