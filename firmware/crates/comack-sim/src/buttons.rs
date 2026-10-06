//! Button inputs for the desktop: typed commands or a test script.

use std::collections::VecDeque;
use std::io::BufRead;
use std::sync::mpsc::{self, Receiver};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use comack_core::application::ports::ButtonInput;
use comack_core::domain::ButtonLevels;

/// Presses queued from anywhere; each is held for exactly one sample and
/// followed by a release, so consecutive presses register as separate edges.
#[derive(Clone, Debug, Default)]
pub struct ScriptedButtons {
    queue: Arc<Mutex<VecDeque<ButtonLevels>>>,
}

impl ScriptedButtons {
    fn queue(&self) -> MutexGuard<'_, VecDeque<ButtonLevels>> {
        // The queue holds plain values, so a panicked holder cannot corrupt it.
        self.queue.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Queue a press of the buttons set in `levels`.
    pub fn press(&self, levels: ButtonLevels) {
        let mut q = self.queue();
        q.push_back(levels);
        q.push_back(ButtonLevels::default());
    }

    /// Samples not yet consumed.
    pub fn pending(&self) -> usize {
        self.queue().len()
    }
}

impl ButtonInput for ScriptedButtons {
    fn levels(&mut self) -> ButtonLevels {
        self.queue().pop_front().unwrap_or_default()
    }
}

/// Map a typed command to a press: `f` forward, `b` back, `t` trigger.
pub fn parse_command(line: &str) -> Option<ButtonLevels> {
    let released = ButtonLevels::default();
    match line.trim() {
        "f" | "forward" | "n" | "next" => Some(ButtonLevels {
            forward: true,
            ..released
        }),
        "b" | "back" | "p" | "prev" => Some(ButtonLevels {
            back: true,
            ..released
        }),
        "t" | "trigger" => Some(ButtonLevels {
            trigger: true,
            ..released
        }),
        _ => None,
    }
}

/// Reads commands from standard input on a background thread.
pub struct StdinButtons {
    commands: Receiver<ButtonLevels>,
    inner: ScriptedButtons,
}

impl StdinButtons {
    /// Start reading standard input.
    pub fn spawn() -> Self {
        let (tx, commands) = mpsc::channel();
        std::thread::spawn(move || {
            for line in std::io::stdin().lock().lines() {
                let Ok(line) = line else { break };
                match parse_command(&line) {
                    Some(press) => {
                        if tx.send(press).is_err() {
                            break;
                        }
                    }
                    None => eprintln!("commands: f (forward), b (back), t (trigger)"),
                }
            }
        });
        Self {
            commands,
            inner: ScriptedButtons::default(),
        }
    }
}

impl ButtonInput for StdinButtons {
    fn levels(&mut self) -> ButtonLevels {
        while let Ok(press) = self.commands.try_recv() {
            self.inner.press(press);
        }
        self.inner.levels()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn press_is_followed_by_release() {
        let mut buttons = ScriptedButtons::default();
        let fwd = ButtonLevels {
            forward: true,
            ..ButtonLevels::default()
        };
        buttons.press(fwd);
        buttons.press(fwd);
        assert_eq!(buttons.pending(), 4);
        assert_eq!(buttons.levels(), fwd);
        assert_eq!(buttons.levels(), ButtonLevels::default());
        assert_eq!(buttons.levels(), fwd);
        assert_eq!(buttons.levels(), ButtonLevels::default());
        assert_eq!(buttons.levels(), ButtonLevels::default(), "idle when empty");
    }

    #[test]
    fn parses_commands() {
        assert!(parse_command("f\n").unwrap().forward);
        assert!(parse_command(" back ").unwrap().back);
        assert!(parse_command("t").unwrap().trigger);
        assert_eq!(parse_command("x"), None);
    }
}
