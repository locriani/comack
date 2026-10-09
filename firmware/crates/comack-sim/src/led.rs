//! Simulator stand-in for the device LED.

use log::info;

use comack_core::application::ports::PressFeedback;

/// Logs LED state changes instead of lighting hardware.
#[derive(Clone, Copy, Default)]
pub struct LogLed;

impl PressFeedback for LogLed {
    fn set_led(&mut self, on: bool) {
        if on {
            info!("LED: on");
        } else {
            info!("LED: off");
        }
    }
}
