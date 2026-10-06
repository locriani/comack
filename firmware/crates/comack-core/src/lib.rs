//! Hardware-agnostic core of the Comack reTerminal E1001 firmware.
//!
//! The crate is organised in Clean Architecture layers, with dependencies
//! pointing inwards only:
//!
//! * [`domain`]: entities and pure rules (frames, issues, browsing, buttons,
//!   audio levels, Wi-Fi rotation). No I/O, no traits to implement.
//! * [`application`]: the two use cases ([`application::ComicViewer`] and
//!   [`application::AudioStreamer`]) and the ports they depend on.
//! * [`adapters`]: implementations of those ports in terms of smaller,
//!   framework-facing boundaries: HTTP and WebSocket over any TCP stream, the
//!   e-paper controller over `embedded-hal`, buttons over GPIO pins.
//!
//! Frameworks (ESP-IDF, MicroLink, `std`) live outside this crate, in the
//! `esp` binary and the `comack-sim` desktop simulator, which plug concrete
//! drivers into these ports.
//!
//! The crate is `no_std`, allocation-free and free of `unsafe`, so every rule
//! is unit-testable on the host.
#![cfg_attr(not(test), no_std)]
// Tests build whole frames on the stack and cast freely when generating data.
#![cfg_attr(
    test,
    allow(clippy::large_stack_arrays, clippy::cast_possible_truncation)
)]

pub mod adapters;
pub mod application;
pub mod config;
pub mod domain;
