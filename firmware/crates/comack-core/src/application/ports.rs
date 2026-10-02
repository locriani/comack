//! Boundaries between the use cases and the outside world.
//!
//! Each port is deliberately small (Interface Segregation) and expressed in
//! domain terms, so use cases do not know about HTTP, WebSockets, SPI or
//! MicroLink (Dependency Inversion).

use core::fmt::Debug;

use crate::domain::{ButtonLevels, Frame, Instant, Issue};

/// A monotonic clock.
pub trait Clock {
    /// The current time.
    fn now(&self) -> Instant;
}

/// Somewhere a frame can be shown: the e-paper panel, a file, a test double.
pub trait FrameDisplay {
    /// Why showing failed.
    type Error: Debug;

    /// Show `frame` (see [`Frame`] for the pixel format).
    fn show(&mut self, frame: &Frame) -> Result<(), Self::Error>;
}

/// The front-panel buttons.
pub trait ButtonInput {
    /// Sample which buttons are held down right now.
    fn levels(&mut self) -> ButtonLevels;
}

/// Read access to the published comics.
pub trait ComicSource {
    /// Why a lookup failed.
    type Error: Debug;

    /// The newest revealed issue, or `None` if nothing has been revealed yet.
    fn latest_issue(&mut self) -> Result<Option<Issue>, Self::Error>;

    /// Load `issue` into `frame`. On error the frame contents are unspecified.
    fn fetch(&mut self, issue: Issue, frame: &mut Frame) -> Result<(), Self::Error>;
}

/// Asks the server to generate and reveal a comic now, as if the trigger
/// phrase had been spoken.
pub trait ComicTrigger {
    /// Why the request failed.
    type Error: Debug;

    /// Fire the trigger.
    fn trigger(&mut self) -> Result<(), Self::Error>;
}

/// A source of 16-bit mono PCM audio.
pub trait AudioSource {
    /// Why reading failed.
    type Error: Debug;

    /// Fill `buffer` with up to `buffer.len()` samples, blocking until they
    /// are available. Returns how many samples were written.
    fn read(&mut self, buffer: &mut [i16]) -> Result<usize, Self::Error>;
}

/// Opens audio sessions to the server.
pub trait AudioUplink {
    /// An open session.
    type Session: AudioSession;
    /// Why opening failed.
    type Error: Debug;

    /// Open a new session.
    fn open(&mut self) -> Result<Self::Session, Self::Error>;
}

/// An open audio session. Dropping it closes the session.
pub trait AudioSession {
    /// Why sending failed. Any error means the session is dead.
    type Error: Debug;

    /// Deliver one chunk of PCM samples.
    fn send(&mut self, pcm: &[i16]) -> Result<(), Self::Error>;
}
