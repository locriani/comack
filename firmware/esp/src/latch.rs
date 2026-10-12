//! A one-way flag threads can block on.

use std::sync::{Condvar, Mutex, PoisonError};

/// Starts unset; once set, stays set and releases every waiter.
#[derive(Debug, Default)]
pub struct Latch {
    set: Mutex<bool>,
    changed: Condvar,
}

impl Latch {
    pub const fn new() -> Self {
        Self {
            set: Mutex::new(false),
            changed: Condvar::new(),
        }
    }

    pub fn set(&self) {
        *self.set.lock().unwrap_or_else(PoisonError::into_inner) = true;
        self.changed.notify_all();
    }

    /// Whether the latch has been set — for bounded waits.
    pub fn is_set(&self) -> bool {
        *self.set.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Block until set. Prefer a polling loop when the e-paper / setup
    /// chord must stay responsive (see `wait_for_wifi` in main).
    #[allow(dead_code)]
    pub fn wait(&self) {
        let guard = self.set.lock().unwrap_or_else(PoisonError::into_inner);
        drop(
            self.changed
                .wait_while(guard, |set| !*set)
                .unwrap_or_else(PoisonError::into_inner),
        );
    }
}
