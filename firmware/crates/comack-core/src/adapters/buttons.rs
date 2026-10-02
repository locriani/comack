//! [`ButtonInput`] over three active-low GPIO inputs with pull-ups.

use embedded_hal::digital::InputPin;

use crate::application::ports::ButtonInput;
use crate::domain::ButtonLevels;

/// The reTerminal's front buttons: pressed pulls the pin low. A pin that
/// cannot be read counts as released.
pub struct ActiveLowButtons<F, B, T> {
    forward: F,
    back: B,
    trigger: T,
}

impl<F: InputPin, B: InputPin, T: InputPin> ActiveLowButtons<F, B, T> {
    /// Wrap the three configured input pins.
    pub const fn new(forward: F, back: B, trigger: T) -> Self {
        Self {
            forward,
            back,
            trigger,
        }
    }
}

impl<F: InputPin, B: InputPin, T: InputPin> ButtonInput for ActiveLowButtons<F, B, T> {
    fn levels(&mut self) -> ButtonLevels {
        ButtonLevels {
            forward: self.forward.is_low().unwrap_or(false),
            back: self.back.is_low().unwrap_or(false),
            trigger: self.trigger.is_low().unwrap_or(false),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use core::convert::Infallible;
    use embedded_hal::digital::{ErrorKind, ErrorType};

    struct Level(bool);

    impl ErrorType for Level {
        type Error = Infallible;
    }

    impl InputPin for Level {
        fn is_high(&mut self) -> Result<bool, Infallible> {
            Ok(self.0)
        }
        fn is_low(&mut self) -> Result<bool, Infallible> {
            Ok(!self.0)
        }
    }

    struct Broken;

    impl ErrorType for Broken {
        type Error = ErrorKind;
    }

    impl InputPin for Broken {
        fn is_high(&mut self) -> Result<bool, ErrorKind> {
            Err(ErrorKind::Other)
        }
        fn is_low(&mut self) -> Result<bool, ErrorKind> {
            Err(ErrorKind::Other)
        }
    }

    #[test]
    fn low_means_pressed() {
        let mut buttons = ActiveLowButtons::new(Level(false), Level(true), Level(false));
        assert_eq!(
            buttons.levels(),
            ButtonLevels {
                forward: true,
                back: false,
                trigger: true
            }
        );
    }

    #[test]
    fn unreadable_pin_is_released() {
        let mut buttons = ActiveLowButtons::new(Broken, Level(false), Broken);
        assert_eq!(
            buttons.levels(),
            ButtonLevels {
                forward: false,
                back: true,
                trigger: false
            }
        );
    }
}
