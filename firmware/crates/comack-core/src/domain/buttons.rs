//! Front-panel buttons: edge detection and press interpretation.

use super::issue::Direction;

/// Instantaneous button state; `true` means held down.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ButtonLevels {
    /// Right white button: next comic.
    pub forward: bool,
    /// Left white button: previous comic.
    pub back: bool,
    /// Green button: same as saying the trigger phrase.
    pub trigger: bool,
}

/// Reports the moment a button goes down. Sampling at a fixed tick (50 ms on
/// the device) debounces the contacts.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct EdgeDetector {
    was_pressed: bool,
}

impl EdgeDetector {
    /// Feed the current level; `true` on a released-to-pressed transition.
    pub fn rising(&mut self, pressed: bool) -> bool {
        let edge = pressed && !self.was_pressed;
        self.was_pressed = pressed;
        edge
    }
}

/// What the reader asked for during one tick.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Presses {
    /// A browse button was pressed. Forward wins if both went down together.
    pub navigate: Option<Direction>,
    /// The trigger button was pressed.
    pub trigger: bool,
}

/// Turns sampled [`ButtonLevels`] into [`Presses`].
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ButtonPanel {
    forward: EdgeDetector,
    back: EdgeDetector,
    trigger: EdgeDetector,
}

impl ButtonPanel {
    /// Feed one sample.
    pub fn update(&mut self, levels: ButtonLevels) -> Presses {
        let forward = self.forward.rising(levels.forward);
        let back = self.back.rising(levels.back);
        let navigate = if forward {
            Some(Direction::Forward)
        } else if back {
            Some(Direction::Back)
        } else {
            None
        };
        Presses {
            navigate,
            trigger: self.trigger.rising(levels.trigger),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const RELEASED: ButtonLevels = ButtonLevels {
        forward: false,
        back: false,
        trigger: false,
    };

    #[test]
    fn edge_fires_once_per_press() {
        let mut edge = EdgeDetector::default();
        assert!(!edge.rising(false));
        assert!(edge.rising(true));
        assert!(!edge.rising(true), "held");
        assert!(!edge.rising(false));
        assert!(edge.rising(true), "pressed again");
    }

    #[test]
    fn idle_panel_reports_nothing() {
        let mut panel = ButtonPanel::default();
        assert_eq!(panel.update(RELEASED), Presses::default());
    }

    #[test]
    fn forward_and_back_map_to_directions() {
        let mut panel = ButtonPanel::default();
        let fwd = ButtonLevels {
            forward: true,
            ..RELEASED
        };
        assert_eq!(panel.update(fwd).navigate, Some(Direction::Forward));
        assert_eq!(panel.update(fwd).navigate, None, "held");
        panel.update(RELEASED);
        let back = ButtonLevels {
            back: true,
            ..RELEASED
        };
        assert_eq!(panel.update(back).navigate, Some(Direction::Back));
    }

    #[test]
    fn forward_wins_a_simultaneous_press() {
        let mut panel = ButtonPanel::default();
        let both = ButtonLevels {
            forward: true,
            back: true,
            ..RELEASED
        };
        assert_eq!(panel.update(both).navigate, Some(Direction::Forward));
    }

    #[test]
    fn back_edge_is_not_lost_while_forward_is_held() {
        let mut panel = ButtonPanel::default();
        panel.update(ButtonLevels {
            forward: true,
            ..RELEASED
        });
        let presses = panel.update(ButtonLevels {
            forward: true,
            back: true,
            ..RELEASED
        });
        assert_eq!(presses.navigate, Some(Direction::Back));
    }

    #[test]
    fn trigger_is_independent_of_navigation() {
        let mut panel = ButtonPanel::default();
        let all = ButtonLevels {
            forward: true,
            back: false,
            trigger: true,
        };
        let presses = panel.update(all);
        assert!(presses.trigger);
        assert_eq!(presses.navigate, Some(Direction::Forward));
        assert!(!panel.update(all).trigger, "held");
    }
}
