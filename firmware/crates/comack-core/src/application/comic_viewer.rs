//! Use case: keep the e-paper showing the right comic.
//!
//! Polls the server for the latest revealed issue, shows it, lets the reader
//! browse older issues with the front buttons (snapping back after a minute
//! idle) and forwards the green trigger button to the server.

use core::time::Duration;

use log::{info, warn};

use super::ports::{ButtonInput, Clock, ComicSource, ComicTrigger, FrameDisplay};
use crate::domain::{
    black_pixel_count, ButtonPanel, Direction, Frame, Instant, Issue, Navigator, FRAME_PIXELS,
};

/// Timing of the viewer loop.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ViewerConfig {
    /// How often to ask the server for the latest issue. The server only
    /// reveals a comic once the room goes quiet, so polling is cheap enough.
    pub poll_interval: Duration,
    /// After a trigger press, poll again this soon: a held comic is revealed
    /// immediately.
    pub trigger_repoll_delay: Duration,
    /// Return to the latest issue after this long without a browse press.
    pub browse_timeout: Duration,
    /// Button sampling period; also debounces the buttons.
    pub tick: Duration,
}

impl Default for ViewerConfig {
    fn default() -> Self {
        Self {
            poll_interval: Duration::from_secs(10),
            trigger_repoll_delay: Duration::from_secs(2),
            browse_timeout: Duration::from_secs(60),
            tick: Duration::from_millis(50),
        }
    }
}

/// The comic viewer. Call [`ComicViewer::tick`] in a loop.
pub struct ComicViewer<'f, S, D, B, C> {
    comics: S,
    display: D,
    buttons: B,
    clock: C,
    frame: &'f mut Frame,
    navigator: Navigator,
    panel: ButtonPanel,
    next_poll: Instant,
    config: ViewerConfig,
}

impl<'f, S, D, B, C> ComicViewer<'f, S, D, B, C>
where
    S: ComicSource + ComicTrigger,
    D: FrameDisplay,
    B: ButtonInput,
    C: Clock,
{
    /// `frame` is the download buffer; on the device it lives in PSRAM.
    /// The first tick polls the server straight away.
    pub fn new(
        comics: S,
        display: D,
        buttons: B,
        clock: C,
        frame: &'f mut Frame,
        config: ViewerConfig,
    ) -> Self {
        Self {
            comics,
            display,
            buttons,
            clock,
            frame,
            navigator: Navigator::new(config.browse_timeout),
            panel: ButtonPanel::default(),
            next_poll: Instant::ZERO,
            config,
        }
    }

    /// Run one step; returns how long to wait before the next.
    pub fn tick(&mut self) -> Duration {
        let now = self.clock.now();
        if now >= self.next_poll {
            self.next_poll = now + self.config.poll_interval;
            self.poll(now);
        }

        let presses = self.panel.update(self.buttons.levels());
        if presses.trigger {
            self.on_trigger();
        }
        if let Some(direction) = presses.navigate {
            self.on_navigate(direction);
        }
        self.config.tick
    }

    /// Browsing state, for diagnostics.
    pub fn navigator(&self) -> &Navigator {
        &self.navigator
    }

    /// The display adapter.
    pub fn display(&self) -> &D {
        &self.display
    }

    fn poll(&mut self, now: Instant) {
        match self.comics.latest_issue() {
            Ok(Some(latest)) => self.navigator.report_latest(latest),
            Ok(None) => {}
            Err(e) => warn!("Could not fetch the latest issue: {e:?}"),
        }
        if let Some(issue) = self.navigator.pending_refresh(now) {
            if self.show(issue) {
                self.navigator.mark_shown(issue);
            }
        }
    }

    fn on_trigger(&mut self) {
        info!("Button: trigger");
        if let Err(e) = self.comics.trigger() {
            warn!("Trigger request failed: {e:?}");
        }
        // Stop browsing and look for the new comic soon.
        self.navigator.end_browse();
        self.next_poll = self.clock.now() + self.config.trigger_repoll_delay;
    }

    fn on_navigate(&mut self, direction: Direction) {
        if self.navigator.shown().is_none() {
            return;
        }
        self.navigator.begin_browse(self.clock.now());
        // Walk past deleted issues. A failed download also counts as a gap.
        for target in self.navigator.browse_targets(direction) {
            let verb = match direction {
                Direction::Forward => "forward",
                Direction::Back => "back",
            };
            info!("Button: {verb} to {target}");
            if self.show(target) {
                self.navigator.mark_shown(target);
                break;
            }
        }
    }

    /// Download and display `issue`; `true` once it is on screen.
    fn show(&mut self, issue: Issue) -> bool {
        info!("Fetching comic {issue}...");
        if let Err(e) = self.comics.fetch(issue, self.frame) {
            warn!("Could not fetch comic {issue}: {e:?}");
            return false;
        }
        info!(
            "Comic {issue} received ({}/{FRAME_PIXELS} black pixels). Updating e-paper.",
            black_pixel_count(self.frame)
        );
        match self.display.show(self.frame) {
            Ok(()) => true,
            Err(e) => {
                warn!("Display refresh failed: {e:?}");
                false
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::{ButtonLevels, FRAME_BYTES};
    use std::cell::{Cell, RefCell};
    use std::collections::{BTreeSet, VecDeque};
    use std::rc::Rc;
    use std::vec::Vec;

    /// In-memory server: issues 1..=latest exist unless deleted; each issue's
    /// frame is filled with its number so the display can tell them apart.
    #[derive(Default)]
    struct FakeServer {
        latest: Option<u32>,
        deleted: BTreeSet<u32>,
        broken: BTreeSet<u32>,
        offline: bool,
        polls: u32,
        triggers: u32,
        reveal_on_trigger: bool,
    }

    #[derive(Clone, Default)]
    struct Comics(Rc<RefCell<FakeServer>>);

    #[derive(Debug, PartialEq)]
    enum FakeError {
        Offline,
        NotFound,
    }

    impl ComicSource for Comics {
        type Error = FakeError;

        fn latest_issue(&mut self) -> Result<Option<Issue>, FakeError> {
            let mut s = self.0.borrow_mut();
            s.polls += 1;
            if s.offline {
                return Err(FakeError::Offline);
            }
            Ok(s.latest.and_then(Issue::new))
        }

        fn fetch(&mut self, issue: Issue, frame: &mut Frame) -> Result<(), FakeError> {
            let s = self.0.borrow();
            let n = issue.get();
            if s.offline {
                return Err(FakeError::Offline);
            }
            if s.deleted.contains(&n) || s.broken.contains(&n) || s.latest.is_none_or(|l| n > l) {
                return Err(FakeError::NotFound);
            }
            frame.fill(u8::try_from(n).unwrap());
            Ok(())
        }
    }

    impl ComicTrigger for Comics {
        type Error = FakeError;

        fn trigger(&mut self) -> Result<(), FakeError> {
            let mut s = self.0.borrow_mut();
            s.triggers += 1;
            if s.offline {
                return Err(FakeError::Offline);
            }
            if s.reveal_on_trigger {
                s.latest = Some(s.latest.unwrap_or(0) + 1);
            }
            Ok(())
        }
    }

    /// Records the first byte (= issue number) of every frame shown.
    #[derive(Clone, Default)]
    struct Screen {
        shown: Rc<RefCell<Vec<u8>>>,
        fail: Rc<Cell<bool>>,
    }

    impl FrameDisplay for Screen {
        type Error = &'static str;

        fn show(&mut self, frame: &Frame) -> Result<(), Self::Error> {
            if self.fail.get() {
                return Err("panel fault");
            }
            self.shown.borrow_mut().push(frame[0]);
            Ok(())
        }
    }

    #[derive(Clone, Default)]
    struct Buttons(Rc<RefCell<VecDeque<ButtonLevels>>>);

    impl Buttons {
        /// Hold `levels` for the next tick only; later ticks read released.
        fn tap(&self, levels: ButtonLevels) {
            self.0.borrow_mut().push_back(levels);
        }
    }

    impl ButtonInput for Buttons {
        fn levels(&mut self) -> ButtonLevels {
            self.0.borrow_mut().pop_front().unwrap_or_default()
        }
    }

    #[derive(Clone, Default)]
    struct FakeClock(Rc<Cell<Instant>>);

    impl FakeClock {
        fn advance(&self, by: Duration) {
            self.0.set(self.0.get() + by);
        }
    }

    impl Clock for FakeClock {
        fn now(&self) -> Instant {
            self.0.get()
        }
    }

    const FORWARD: ButtonLevels = ButtonLevels {
        forward: true,
        back: false,
        trigger: false,
    };
    const BACK: ButtonLevels = ButtonLevels {
        forward: false,
        back: true,
        trigger: false,
    };
    const TRIGGER: ButtonLevels = ButtonLevels {
        forward: false,
        back: false,
        trigger: true,
    };

    struct Harness {
        comics: Comics,
        screen: Screen,
        buttons: Buttons,
        clock: FakeClock,
    }

    impl Harness {
        fn new(latest: Option<u32>) -> Self {
            let comics = Comics::default();
            comics.0.borrow_mut().latest = latest;
            Self {
                comics,
                screen: Screen::default(),
                buttons: Buttons::default(),
                clock: FakeClock::default(),
            }
        }

        fn viewer<'f>(
            &self,
            frame: &'f mut Frame,
        ) -> ComicViewer<'f, Comics, Screen, Buttons, FakeClock> {
            ComicViewer::new(
                self.comics.clone(),
                self.screen.clone(),
                self.buttons.clone(),
                self.clock.clone(),
                frame,
                ViewerConfig::default(),
            )
        }

        fn shown(&self) -> Vec<u8> {
            self.screen.shown.borrow().clone()
        }

        fn server(&self) -> std::cell::RefMut<'_, FakeServer> {
            self.comics.0.borrow_mut()
        }
    }

    fn frame() -> Frame {
        [0xFF; FRAME_BYTES]
    }

    #[test]
    fn shows_latest_issue_on_first_tick() {
        let h = Harness::new(Some(3));
        let mut buf = frame();
        let mut viewer = h.viewer(&mut buf);
        assert_eq!(viewer.tick(), Duration::from_millis(50));
        assert_eq!(h.shown(), [3]);
        assert_eq!(viewer.navigator().shown(), Issue::new(3));
    }

    #[test]
    fn polls_only_every_poll_interval() {
        let h = Harness::new(Some(1));
        let mut buf = frame();
        let mut viewer = h.viewer(&mut buf);
        viewer.tick();
        h.clock.advance(Duration::from_secs(9));
        viewer.tick();
        assert_eq!(h.server().polls, 1);
        h.clock.advance(Duration::from_secs(1));
        viewer.tick();
        assert_eq!(h.server().polls, 2);
    }

    #[test]
    fn does_not_redraw_an_unchanged_issue() {
        let h = Harness::new(Some(2));
        let mut buf = frame();
        let mut viewer = h.viewer(&mut buf);
        viewer.tick();
        h.clock.advance(Duration::from_secs(10));
        viewer.tick();
        assert_eq!(h.shown(), [2]);
    }

    #[test]
    fn shows_newly_revealed_issue_on_next_poll() {
        let h = Harness::new(Some(2));
        let mut buf = frame();
        let mut viewer = h.viewer(&mut buf);
        viewer.tick();
        h.server().latest = Some(3);
        h.clock.advance(Duration::from_secs(10));
        viewer.tick();
        assert_eq!(h.shown(), [2, 3]);
    }

    #[test]
    fn nothing_shown_before_first_reveal() {
        let h = Harness::new(None);
        let mut buf = frame();
        let mut viewer = h.viewer(&mut buf);
        viewer.tick();
        assert!(h.shown().is_empty());
    }

    #[test]
    fn server_outage_keeps_last_known_state_and_retries() {
        let h = Harness::new(Some(4));
        let mut buf = frame();
        let mut viewer = h.viewer(&mut buf);
        h.server().offline = true;
        viewer.tick();
        assert!(h.shown().is_empty());
        h.server().offline = false;
        h.clock.advance(Duration::from_secs(10));
        viewer.tick();
        assert_eq!(h.shown(), [4]);
    }

    #[test]
    fn failed_download_is_retried_on_next_poll() {
        let h = Harness::new(Some(5));
        h.server().broken.insert(5);
        let mut buf = frame();
        let mut viewer = h.viewer(&mut buf);
        viewer.tick();
        assert!(h.shown().is_empty());
        h.server().broken.clear();
        h.clock.advance(Duration::from_secs(10));
        viewer.tick();
        assert_eq!(h.shown(), [5]);
    }

    #[test]
    fn display_failure_is_not_marked_shown() {
        let h = Harness::new(Some(5));
        h.screen.fail.set(true);
        let mut buf = frame();
        let mut viewer = h.viewer(&mut buf);
        viewer.tick();
        assert_eq!(viewer.navigator().shown(), None);
        h.screen.fail.set(false);
        h.clock.advance(Duration::from_secs(10));
        viewer.tick();
        assert_eq!(h.shown(), [5]);
    }

    #[test]
    fn back_button_shows_previous_issue_and_holds_it() {
        let h = Harness::new(Some(5));
        let mut buf = frame();
        let mut viewer = h.viewer(&mut buf);
        viewer.tick();
        h.buttons.tap(BACK);
        viewer.tick();
        assert_eq!(h.shown(), [5, 4]);
        // Polling during the browse window does not snap back.
        h.clock.advance(Duration::from_secs(30));
        viewer.tick();
        assert_eq!(h.shown(), [5, 4]);
    }

    #[test]
    fn snaps_back_to_latest_after_browse_timeout() {
        let h = Harness::new(Some(5));
        let mut buf = frame();
        let mut viewer = h.viewer(&mut buf);
        viewer.tick();
        h.buttons.tap(BACK);
        viewer.tick();
        h.clock.advance(Duration::from_secs(60));
        viewer.tick();
        assert_eq!(h.shown(), [5, 4, 5]);
    }

    #[test]
    fn browsing_skips_deleted_issues() {
        let h = Harness::new(Some(5));
        h.server().deleted.extend([4, 3]);
        let mut buf = frame();
        let mut viewer = h.viewer(&mut buf);
        viewer.tick();
        h.buttons.tap(BACK);
        viewer.tick();
        assert_eq!(h.shown(), [5, 2]);
        h.buttons.tap(FORWARD);
        viewer.tick();
        viewer.tick();
        assert_eq!(h.shown(), [5, 2, 5]);
    }

    #[test]
    fn browsing_stops_at_both_ends() {
        let h = Harness::new(Some(2));
        let mut buf = frame();
        let mut viewer = h.viewer(&mut buf);
        viewer.tick();
        h.buttons.tap(FORWARD);
        viewer.tick();
        viewer.tick();
        assert_eq!(h.shown(), [2], "already at latest");
        h.buttons.tap(BACK);
        viewer.tick();
        viewer.tick();
        h.buttons.tap(BACK);
        viewer.tick();
        assert_eq!(h.shown(), [2, 1], "nothing before #1");
    }

    #[test]
    fn browse_buttons_ignored_until_something_is_shown() {
        let h = Harness::new(None);
        let mut buf = frame();
        let mut viewer = h.viewer(&mut buf);
        h.buttons.tap(BACK);
        viewer.tick();
        assert!(!viewer.navigator().is_browsing(h.clock.now()));
    }

    #[test]
    fn trigger_fires_request_and_repolls_soon() {
        let h = Harness::new(Some(5));
        h.server().reveal_on_trigger = true;
        let mut buf = frame();
        let mut viewer = h.viewer(&mut buf);
        viewer.tick();
        h.buttons.tap(BACK);
        viewer.tick();
        h.buttons.tap(TRIGGER);
        viewer.tick();
        assert_eq!(h.server().triggers, 1);
        assert!(
            !viewer.navigator().is_browsing(h.clock.now()),
            "trigger ends browsing"
        );
        h.clock.advance(Duration::from_secs(2));
        viewer.tick();
        assert_eq!(h.shown(), [5, 4, 6]);
    }

    #[test]
    fn failed_trigger_still_repolls() {
        let h = Harness::new(Some(1));
        let mut buf = frame();
        let mut viewer = h.viewer(&mut buf);
        viewer.tick();
        h.server().offline = true;
        h.buttons.tap(TRIGGER);
        viewer.tick();
        h.server().offline = false;
        h.clock.advance(Duration::from_secs(2));
        viewer.tick();
        assert_eq!(h.server().polls, 2);
    }
}
