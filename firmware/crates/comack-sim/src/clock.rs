//! Monotonic time from `std::time::Instant`.

use comack_core::application::ports::Clock;
use comack_core::domain::Instant;

/// Measures time from its creation.
#[derive(Clone, Copy, Debug)]
pub struct StdClock(std::time::Instant);

impl StdClock {
    /// Start the clock at zero now.
    pub fn new() -> Self {
        Self(std::time::Instant::now())
    }
}

impl Default for StdClock {
    fn default() -> Self {
        Self::new()
    }
}

impl Clock for StdClock {
    fn now(&self) -> Instant {
        Instant::from_elapsed(self.0.elapsed())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn is_monotonic_from_zero() {
        let clock = StdClock::new();
        let a = clock.now();
        std::thread::sleep(Duration::from_millis(2));
        let b = clock.now();
        assert!(a < b);
        assert!(a.elapsed() < Duration::from_secs(1));
    }
}
