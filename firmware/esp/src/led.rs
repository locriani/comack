//! The board's onboard LED as the press indicator.

use esp_idf_svc::hal::gpio::{Output, PinDriver};
use log::warn;

use comack_core::application::ports::PressFeedback;

/// The reTerminal E1001/E1002's single white LED: inverted logic, LOW means
/// on (Seeed's board wiki, "Arduino Cookbook: Onboard Peripherals").
pub struct InvertedLed<'d>(PinDriver<'d, Output>);

impl<'d> InvertedLed<'d> {
    /// Wrap the configured output pin driving the LED.
    pub const fn new(pin: PinDriver<'d, Output>) -> Self {
        Self(pin)
    }
}

impl PressFeedback for InvertedLed<'_> {
    fn set_led(&mut self, on: bool) {
        // Inverted: pull the pin low to light it.
        let result = if on {
            self.0.set_low()
        } else {
            self.0.set_high()
        };
        if let Err(e) = result {
            warn!("LED: {e:?}");
        }
    }
}
