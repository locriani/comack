//! Transient press indication: how long the onboard LED stays lit.

use core::time::Duration;

use super::time::Instant;

/// Pure dwell timer for the press LED: on at the press, off `dwell` later,
/// re-armed by every fresh press. The viewer ticks it; the adapter only
/// switches the pin.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct LedDwell {
    on: bool,
    off_at: Option<Instant>,
}

impl LedDwell {
    /// A press at `now`. Returns whether the LED state changed (a re-press
    /// inside the dwell restarts it without a redundant switch-on).
    pub fn press(&mut self, now: Instant, dwell: Duration) -> bool {
        self.off_at = Some(now + dwell);
        if self.on {
            false
        } else {
            self.on = true;
            true
        }
    }

    /// Advance to `now`. Returns whether the LED state changed (only an
    /// expired dwell can change it).
    pub fn tick(&mut self, now: Instant) -> bool {
        if self.on && self.off_at.is_some_and(|off_at| now >= off_at) {
            self.on = false;
            self.off_at = None;
            true
        } else {
            false
        }
    }

    /// Whether the LED should be lit right now.
    pub const fn is_on(&self) -> bool {
        self.on
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const DWELL: Duration = Duration::from_millis(150);

    fn at(millis: u64) -> Instant {
        Instant::from_micros(millis * 1_000)
    }

    #[test]
    fn dwell_turns_on_at_press_and_off_at_dwell_end() {
        let mut led = LedDwell::default();
        assert!(led.press(at(1_000), DWELL), "first press switches on");
        assert!(led.is_on());
        // 149 ms in: still on.
        assert!(!led.tick(at(1_149)));
        // The dwell has elapsed: off.
        assert!(led.tick(at(1_150)));
        assert!(!led.is_on());
        assert!(!led.tick(at(1_151)), "stays off");
    }

    #[test]
    fn a_repress_restarts_the_dwell_without_a_second_switch_on() {
        let mut led = LedDwell::default();
        assert!(led.press(at(1_000), DWELL));
        // Second press 100 ms in: still lit, no redundant switch-on.
        assert!(!led.press(at(1_100), DWELL));
        assert!(led.is_on());
        // The original deadline (1_150) has passed, but the dwell restarted:
        // still on until 1_250.
        assert!(!led.tick(at(1_150)));
        assert!(led.tick(at(1_250)));
        assert!(!led.is_on());
    }

    #[test]
    fn a_zero_dwell_turns_off_on_the_same_tick() {
        let mut led = LedDwell::default();
        assert!(led.press(at(1_000), Duration::ZERO));
        assert!(led.tick(at(1_000)));
        assert!(!led.is_on());
    }
}
