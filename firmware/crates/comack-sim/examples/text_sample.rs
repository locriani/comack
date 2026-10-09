//! Renders sample text through the real frame renderer and writes it as
//! a PBM, proving the e-paper text path without hardware:
//!
//! ```text
//! cargo run -p comack-sim --example text_sample -- out/text-sample.pbm
//! ```

// The e-paper frame is a stack array by design, matching the device.
#![allow(clippy::large_stack_arrays)]

use std::fs;
use std::process::ExitCode;

use comack_core::domain::{draw_text, Frame, FRAME_BYTES};
use comack_sim::PbmDisplay;

fn main() -> ExitCode {
    let path = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "text-sample.pbm".into());
    let mut frame: Frame = [0xFF; FRAME_BYTES];
    draw_text(&mut frame, 40, 200, "Setup: join WiFi comack-setup");
    draw_text(&mut frame, 40, 220, "then open http://192.168.4.1");
    draw_text(&mut frame, 40, 240, "0123456789 ABCDEFGHIJKLMNOPQRSTUVWXYZ");
    draw_text(&mut frame, 40, 260, "abcdefghijklmnopqrstuvwxyz .,:/=-");
    fs::write(&path, PbmDisplay::encode(&frame)).expect("write PBM");
    println!("wrote {path}");
    ExitCode::SUCCESS
}
