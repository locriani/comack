//! Use cases and the ports (traits) they depend on.
//!
//! Use cases never sleep or block on timers themselves: each `tick` does one
//! step of work and returns how long the caller should wait before the next
//! one. That keeps scheduling in the composition root and makes every step
//! deterministic under test.

pub mod audio_streamer;
pub mod comic_viewer;
pub mod device_config;
pub mod ports;
pub mod provisioning;

pub use audio_streamer::{AudioStreamer, StreamerConfig};
pub use comic_viewer::{ComicViewer, ViewerConfig};
pub use device_config::{load_or_fallback, load_with_source, ConfigSource};
pub use provisioning::{build_config, ProvisionError, Submission};
