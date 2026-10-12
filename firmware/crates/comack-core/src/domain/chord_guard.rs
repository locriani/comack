//! Keeps a multi-button chord (the setup gesture: all three buttons held)
//! from also counting as ordinary presses. A press is held back for a short
//! grace window; if a second button joins in that window, nothing is
//! reported until every button is released again.

use core::time::Duration;

use super::buttons::ButtonLevels;
use super::time::Instant;

/// How long a lone press waits for company before it counts. Long enough
/// for fingers to land together, short enough to feel instant.
pub const CHORD_GRACE: Duration = Duration::from_millis(300);

const NONE: ButtonLevels = ButtonLevels {
    forward: false,
    back: false,
    trigger: false,
};

fn count(levels: ButtonLevels) -> u8 {
    u8::from(levels.forward) + u8::from(levels.back) + u8::from(levels.trigger)
}

fn union(a: ButtonLevels, b: ButtonLevels) -> ButtonLevels {
    ButtonLevels {
        forward: a.forward || b.forward,
        back: a.back || b.back,
        trigger: a.trigger || b.trigger,
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum State {
    /// Nothing pressed.
    Idle,
    /// A press is waiting out the grace window; `seen` is everything pressed
    /// so far (taps released inside the window included).
    Waiting { since: Instant, seen: ButtonLevels },
    /// The press was alone: levels pass through until all are released.
    Passing,
    /// A chord was seen: report nothing until all are released.
    Suppressed,
}

/// Filters the sampled button levels. See the module docs.
#[derive(Clone, Copy, Debug)]
pub struct ChordGuard {
    state: State,
}

impl Default for ChordGuard {
    fn default() -> Self {
        Self::new()
    }
}

impl ChordGuard {
    /// A guard with nothing pressed.
    pub const fn new() -> Self {
        Self { state: State::Idle }
    }

    /// `raw` is what the pins read now; `pressed` adds presses latched since
    /// the last call (taps that ended between samples). Returns the levels
    /// to feed the viewer. Call once per sample.
    pub fn filter(
        &mut self,
        now: Instant,
        raw: ButtonLevels,
        pressed: ButtonLevels,
    ) -> ButtonLevels {
        let pressed = union(raw, pressed);
        let chord = count(raw) >= 2;
        match self.state {
            State::Idle => {
                if count(pressed) > 0 {
                    self.state = if chord {
                        State::Suppressed
                    } else {
                        State::Waiting {
                            since: now,
                            seen: pressed,
                        }
                    };
                }
                NONE
            }
            State::Waiting { since, seen } => {
                let seen = union(seen, pressed);
                if chord {
                    self.state = State::Suppressed;
                    NONE
                } else if now.elapsed().saturating_sub(since.elapsed()) >= CHORD_GRACE {
                    self.state = State::Passing;
                    seen
                } else {
                    self.state = State::Waiting { since, seen };
                    NONE
                }
            }
            State::Passing => {
                if count(pressed) == 0 {
                    self.state = State::Idle;
                    NONE
                } else {
                    pressed
                }
            }
            State::Suppressed => {
                if count(raw) == 0 {
                    self.state = State::Idle;
                }
                NONE
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const FWD: ButtonLevels = ButtonLevels {
        forward: true,
        ..NONE
    };
    const BACK: ButtonLevels = ButtonLevels { back: true, ..NONE };
    const ALL: ButtonLevels = ButtonLevels {
        forward: true,
        back: true,
        trigger: true,
    };

    fn at(ms: u64) -> Instant {
        Instant::from_micros(ms * 1_000)
    }

    #[test]
    fn a_lone_press_counts_after_the_grace_window() {
        let mut guard = ChordGuard::new();
        assert_eq!(guard.filter(at(0), FWD, NONE), NONE);
        assert_eq!(guard.filter(at(100), FWD, NONE), NONE);
        assert_eq!(guard.filter(at(300), FWD, NONE), FWD);
        assert_eq!(guard.filter(at(400), FWD, NONE), FWD);
        assert_eq!(guard.filter(at(500), NONE, NONE), NONE);
    }

    #[test]
    fn a_tap_shorter_than_the_window_is_not_lost() {
        let mut guard = ChordGuard::new();
        assert_eq!(guard.filter(at(0), FWD, NONE), NONE);
        assert_eq!(guard.filter(at(100), NONE, NONE), NONE);
        assert_eq!(guard.filter(at(300), NONE, NONE), FWD);
        assert_eq!(guard.filter(at(350), NONE, NONE), NONE);
    }

    #[test]
    fn a_latched_tap_between_samples_counts() {
        let mut guard = ChordGuard::new();
        assert_eq!(guard.filter(at(0), NONE, BACK), NONE);
        assert_eq!(guard.filter(at(300), NONE, NONE), BACK);
    }

    #[test]
    fn a_chord_reports_nothing_until_all_are_released() {
        let mut guard = ChordGuard::new();
        let both = ButtonLevels {
            forward: true,
            back: true,
            trigger: false,
        };
        // Staggered landing: forward, then everyone inside the window.
        assert_eq!(guard.filter(at(0), FWD, NONE), NONE);
        assert_eq!(guard.filter(at(120), both, NONE), NONE);
        assert_eq!(guard.filter(at(240), ALL, NONE), NONE);
        for ms in [500, 5_000, 10_000] {
            assert_eq!(guard.filter(at(ms), ALL, NONE), NONE);
        }
        // Part-released is still the gesture.
        assert_eq!(guard.filter(at(10_100), FWD, NONE), NONE);
        assert_eq!(guard.filter(at(10_200), NONE, NONE), NONE);
        // Back to normal.
        assert_eq!(guard.filter(at(10_300), BACK, NONE), NONE);
        assert_eq!(guard.filter(at(10_600), BACK, NONE), BACK);
    }

    #[test]
    fn buttons_landing_on_the_same_sample_are_a_chord() {
        let mut guard = ChordGuard::new();
        assert_eq!(guard.filter(at(0), ALL, NONE), NONE);
        assert_eq!(guard.filter(at(400), ALL, NONE), NONE);
    }

    #[test]
    fn two_taps_one_after_the_other_both_count() {
        let mut guard = ChordGuard::new();
        assert_eq!(guard.filter(at(0), FWD, NONE), NONE);
        assert_eq!(guard.filter(at(100), NONE, NONE), NONE);
        assert_eq!(guard.filter(at(150), BACK, NONE), NONE);
        assert_eq!(guard.filter(at(200), NONE, NONE), NONE);
        let out = guard.filter(at(300), NONE, NONE);
        assert!(out.forward && out.back && !out.trigger);
    }
}
