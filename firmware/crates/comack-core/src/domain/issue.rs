//! Comic issue numbers.

use core::fmt;
use core::num::NonZeroU32;

/// A comic issue number. Issues are numbered from 1.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Issue(NonZeroU32);

/// Which way to browse through issues.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Direction {
    /// Towards newer issues.
    Forward,
    /// Towards older issues.
    Back,
}

impl Issue {
    /// The first issue.
    pub const FIRST: Self = Self(NonZeroU32::MIN);

    /// `None` for 0, which is not an issue number.
    pub const fn new(number: u32) -> Option<Self> {
        match NonZeroU32::new(number) {
            Some(n) => Some(Self(n)),
            None => None,
        }
    }

    /// The issue number.
    pub const fn get(self) -> u32 {
        self.0.get()
    }

    /// The neighbouring issue in `direction`, if there is one.
    pub fn step(self, direction: Direction) -> Option<Self> {
        match direction {
            Direction::Forward => self.0.checked_add(1).map(Self),
            Direction::Back => Self::new(self.get() - 1),
        }
    }
}

impl fmt::Display for Issue {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "#{}", self.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zero_is_not_an_issue() {
        assert_eq!(Issue::new(0), None);
        assert_eq!(Issue::new(1), Some(Issue::FIRST));
        assert_eq!(Issue::new(7).map(Issue::get), Some(7));
    }

    #[test]
    fn steps_within_range() {
        let five = Issue::new(5).unwrap();
        assert_eq!(five.step(Direction::Forward), Issue::new(6));
        assert_eq!(five.step(Direction::Back), Issue::new(4));
        assert_eq!(Issue::FIRST.step(Direction::Back), None);
        assert_eq!(Issue::new(u32::MAX).unwrap().step(Direction::Forward), None);
    }

    #[test]
    fn displays_with_hash() {
        extern crate std;
        use std::string::ToString;
        assert_eq!(Issue::new(42).unwrap().to_string(), "#42");
    }
}
