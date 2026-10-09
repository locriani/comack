//! [`ButtonInput`] over three active-low GPIO inputs with pull-ups.

use core::sync::atomic::{AtomicBool, Ordering};

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

/// Presses reported by GPIO interrupts while the main loop was blocked.
///
/// An ISR notification marks a button pressed-since-last-read; the next read
/// ORs the marks into the sampled levels and clears them, so a press that
/// starts and ends inside a blocked window still yields one rising edge.
pub struct PressLatch {
    latched: [AtomicBool; 3],
}

impl PressLatch {
    /// Latch index of the forward button.
    pub const FORWARD: usize = 0;
    /// Latch index of the back button.
    pub const BACK: usize = 1;
    /// Latch index of the trigger button.
    pub const TRIGGER: usize = 2;

    /// No presses latched yet.
    pub const fn new() -> Self {
        Self {
            latched: [
                AtomicBool::new(false),
                AtomicBool::new(false),
                AtomicBool::new(false),
            ],
        }
    }

    /// Record that the button at `index` was pressed since the last read.
    /// ISR-safe: a single relaxed atomic store.
    pub fn mark_pressed(&self, index: usize) {
        self.latched[index].store(true, Ordering::Relaxed);
    }

    /// OR the latched presses into `levels` and clear the latch.
    pub fn consume(&self, levels: &mut ButtonLevels) {
        for (index, pressed) in self.latched.iter().enumerate() {
            if pressed.swap(false, Ordering::Relaxed) {
                match index {
                    Self::FORWARD => levels.forward = true,
                    Self::BACK => levels.back = true,
                    _ => levels.trigger = true,
                }
            }
        }
    }
}

impl Default for PressLatch {
    fn default() -> Self {
        Self::new()
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

    #[test]
    fn an_empty_latch_contributes_nothing() {
        let latch = PressLatch::new();
        let mut levels = ButtonLevels::default();
        latch.consume(&mut levels);
        assert_eq!(levels, ButtonLevels::default());
    }

    #[test]
    fn a_latched_press_yields_one_edge_then_clears() {
        let latch = PressLatch::new();
        latch.mark_pressed(PressLatch::TRIGGER);

        let mut levels = ButtonLevels::default();
        latch.consume(&mut levels);
        assert!(levels.trigger, "latched press becomes an edge");
        assert!(!levels.forward && !levels.back);

        let mut cleared = ButtonLevels::default();
        latch.consume(&mut cleared);
        assert_eq!(cleared, ButtonLevels::default(), "consume clears the latch");
    }

    #[test]
    fn the_latch_ors_into_sampled_levels() {
        let latch = PressLatch::new();
        let mut levels = ButtonLevels {
            forward: true,
            back: false,
            trigger: false,
        };
        latch.mark_pressed(PressLatch::BACK);
        latch.consume(&mut levels);
        assert!(levels.forward, "the sampled level survives");
        assert!(levels.back, "the latched press is ORed in");
        assert!(!levels.trigger);
    }

    #[test]
    fn repeated_marks_within_one_window_collapse_to_one_edge() {
        let latch = PressLatch::new();
        latch.mark_pressed(PressLatch::BACK);
        latch.mark_pressed(PressLatch::BACK);
        latch.mark_pressed(PressLatch::BACK);

        let mut levels = ButtonLevels::default();
        latch.consume(&mut levels);
        assert!(levels.back);
        let mut after = ButtonLevels::default();
        latch.consume(&mut after);
        assert_eq!(after, ButtonLevels::default(), "one rising edge in total");
    }
}
