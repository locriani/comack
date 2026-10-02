//! Which comic the display should show: the latest one, unless the reader is
//! browsing older issues with the front buttons.

use core::time::Duration;

use super::issue::{Direction, Issue};
use super::time::Instant;

/// Tracks the latest revealed issue, the issue on screen and whether the
/// reader is browsing. After `browse_timeout` without a press the display
/// snaps back to the latest issue.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Navigator {
    latest: Option<Issue>,
    shown: Option<Issue>,
    browse_until: Option<Instant>,
    browse_timeout: Duration,
}

impl Navigator {
    /// Nothing known or shown yet.
    pub const fn new(browse_timeout: Duration) -> Self {
        Self {
            latest: None,
            shown: None,
            browse_until: None,
            browse_timeout,
        }
    }

    /// The latest issue the server has revealed.
    pub const fn latest(&self) -> Option<Issue> {
        self.latest
    }

    /// The issue currently on screen.
    pub const fn shown(&self) -> Option<Issue> {
        self.shown
    }

    /// Record the server's latest revealed issue.
    pub fn report_latest(&mut self, issue: Issue) {
        self.latest = Some(issue);
    }

    /// Record that `issue` is now on screen.
    pub fn mark_shown(&mut self, issue: Issue) {
        self.shown = Some(issue);
    }

    /// Whether a recent button press is holding the display on an older issue.
    pub fn is_browsing(&self, now: Instant) -> bool {
        self.browse_until.is_some_and(|until| now < until)
    }

    /// The issue to display now, if the screen is out of date: the latest
    /// issue, once browsing has timed out.
    pub fn pending_refresh(&self, now: Instant) -> Option<Issue> {
        if self.is_browsing(now) {
            return None;
        }
        self.latest.filter(|latest| Some(*latest) != self.shown)
    }

    /// A button press: hold the current position for another `browse_timeout`.
    pub fn begin_browse(&mut self, now: Instant) {
        self.browse_until = Some(now + self.browse_timeout);
    }

    /// Stop browsing so the next refresh shows the latest issue.
    pub fn end_browse(&mut self) {
        self.browse_until = None;
    }

    /// Candidate issues for one step in `direction`, nearest first. Several
    /// candidates exist because deleted issues leave gaps to walk past; the
    /// walk never goes beyond the latest revealed issue.
    pub fn browse_targets(&self, direction: Direction) -> BrowseTargets {
        BrowseTargets {
            next: self.shown.and_then(|shown| shown.step(direction)),
            latest: self.latest,
            direction,
        }
    }
}

/// Iterator returned by [`Navigator::browse_targets`]. It owns its state, so
/// the navigator stays free to change while it is consumed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BrowseTargets {
    next: Option<Issue>,
    latest: Option<Issue>,
    direction: Direction,
}

impl Iterator for BrowseTargets {
    type Item = Issue;

    fn next(&mut self) -> Option<Issue> {
        let current = self
            .next
            .filter(|n| self.latest.is_some_and(|latest| *n <= latest))?;
        self.next = current.step(self.direction);
        Some(current)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::vec::Vec;

    const TIMEOUT: Duration = Duration::from_secs(60);

    fn issue(n: u32) -> Issue {
        Issue::new(n).unwrap()
    }

    fn at(secs: u64) -> Instant {
        Instant::from_elapsed(Duration::from_secs(secs))
    }

    fn targets(nav: &Navigator, direction: Direction) -> Vec<u32> {
        nav.browse_targets(direction).map(Issue::get).collect()
    }

    #[test]
    fn nothing_to_show_until_the_server_reports() {
        let nav = Navigator::new(TIMEOUT);
        assert_eq!(nav.pending_refresh(at(0)), None);
        assert_eq!(nav.latest(), None);
        assert_eq!(nav.shown(), None);
    }

    #[test]
    fn refreshes_to_latest_until_shown() {
        let mut nav = Navigator::new(TIMEOUT);
        nav.report_latest(issue(3));
        assert_eq!(nav.pending_refresh(at(0)), Some(issue(3)));
        nav.mark_shown(issue(3));
        assert_eq!(nav.pending_refresh(at(0)), None);
        nav.report_latest(issue(4));
        assert_eq!(nav.pending_refresh(at(0)), Some(issue(4)));
    }

    #[test]
    fn browsing_holds_the_display_until_timeout() {
        let mut nav = Navigator::new(TIMEOUT);
        nav.report_latest(issue(5));
        nav.mark_shown(issue(4));
        nav.begin_browse(at(10));
        assert!(nav.is_browsing(at(10)));
        assert!(nav.is_browsing(at(69)));
        assert_eq!(nav.pending_refresh(at(69)), None);
        assert!(!nav.is_browsing(at(70)));
        assert_eq!(nav.pending_refresh(at(70)), Some(issue(5)));
    }

    #[test]
    fn another_press_extends_browsing() {
        let mut nav = Navigator::new(TIMEOUT);
        nav.begin_browse(at(0));
        nav.begin_browse(at(50));
        assert!(nav.is_browsing(at(100)));
        assert!(!nav.is_browsing(at(110)));
    }

    #[test]
    fn ending_browse_allows_refresh_immediately() {
        let mut nav = Navigator::new(TIMEOUT);
        nav.report_latest(issue(2));
        nav.mark_shown(issue(1));
        nav.begin_browse(at(0));
        nav.end_browse();
        assert_eq!(nav.pending_refresh(at(0)), Some(issue(2)));
    }

    #[test]
    fn browse_back_walks_down_to_first_issue() {
        let mut nav = Navigator::new(TIMEOUT);
        nav.report_latest(issue(4));
        nav.mark_shown(issue(4));
        assert_eq!(targets(&nav, Direction::Back), [3, 2, 1]);
    }

    #[test]
    fn browse_forward_stops_at_latest() {
        let mut nav = Navigator::new(TIMEOUT);
        nav.report_latest(issue(5));
        nav.mark_shown(issue(2));
        assert_eq!(targets(&nav, Direction::Forward), [3, 4, 5]);
        nav.mark_shown(issue(5));
        assert_eq!(targets(&nav, Direction::Forward), [0u32; 0]);
    }

    #[test]
    fn no_targets_without_a_shown_or_latest_issue() {
        let mut nav = Navigator::new(TIMEOUT);
        assert_eq!(targets(&nav, Direction::Back), [0u32; 0]);
        nav.mark_shown(issue(3));
        assert_eq!(targets(&nav, Direction::Back), [0u32; 0], "latest unknown");
    }

    #[test]
    fn targets_do_not_borrow_the_navigator() {
        let mut nav = Navigator::new(TIMEOUT);
        nav.report_latest(issue(3));
        nav.mark_shown(issue(3));
        for target in nav.browse_targets(Direction::Back) {
            nav.mark_shown(target);
        }
        assert_eq!(nav.shown(), Some(issue(1)));
    }
}
