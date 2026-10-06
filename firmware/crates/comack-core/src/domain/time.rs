//! Monotonic time, independent of any platform clock.

use core::ops::Add;
use core::time::Duration;

/// A point on a monotonic clock, measured from an arbitrary origin (boot).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Instant(Duration);

impl Instant {
    /// The clock origin.
    pub const ZERO: Self = Self(Duration::ZERO);

    /// An instant `elapsed` after the origin.
    pub const fn from_elapsed(elapsed: Duration) -> Self {
        Self(elapsed)
    }

    /// An instant `micros` microseconds after the origin.
    pub const fn from_micros(micros: u64) -> Self {
        Self(Duration::from_micros(micros))
    }

    /// Time since the origin.
    pub const fn elapsed(self) -> Duration {
        self.0
    }
}

impl Add<Duration> for Instant {
    type Output = Self;

    /// Saturates instead of panicking: the clock cannot realistically get there.
    fn add(self, rhs: Duration) -> Self {
        Self(self.0.saturating_add(rhs))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn adds_and_orders() {
        let t = Instant::from_micros(1_000);
        assert_eq!(t + Duration::from_millis(1), Instant::from_micros(2_000));
        assert!(t < t + Duration::from_nanos(1));
        assert_eq!(Instant::ZERO.elapsed(), Duration::ZERO);
        assert_eq!(
            Instant::from_elapsed(Duration::from_secs(3)).elapsed(),
            Duration::from_secs(3)
        );
    }

    #[test]
    fn addition_saturates() {
        let max = Instant::from_elapsed(Duration::MAX);
        assert_eq!(max + Duration::from_secs(1), max);
    }
}
