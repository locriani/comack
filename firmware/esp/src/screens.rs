//! The provisioning and connection e-paper screens: text on the 800×480
//! panel, drawn with the core's scaled glyph renderer. Every screen writes
//! into a caller-provided frame buffer (PSRAM-backed in the composition
//! root — 48 KB does not belong on a task stack) and shows it once; the
//! panel holds the image until the next refresh.

use comack_core::application::ports::FrameDisplay;
use comack_core::domain::{draw_text_scaled, Frame, FRAME_BYTES, FRAME_WIDTH};

/// Text scale for body lines: 3× keeps 44 characters per line readable
/// from across the room. Headings use one step more.
const BODY_SCALE: usize = 3;
const HEADING_SCALE: usize = 4;

/// Glyph advance with spacing (5 px glyph + 1 px gap), per unit of scale.
const ADVANCE: usize = 6;
/// Glyph height with spacing, per unit of scale.
const PITCH: usize = 9;

/// Formats `lines` centred, top to bottom, first line as the heading.
fn layout(lines: &[&str], frame: &mut Frame) {
    *frame = [0xFF; FRAME_BYTES];
    for (n, line) in lines.iter().enumerate() {
        let scale = if n == 0 { HEADING_SCALE } else { BODY_SCALE };
        let width = line.chars().count() * ADVANCE * scale;
        let x = FRAME_WIDTH.saturating_sub(width.min(FRAME_WIDTH)) / 2;
        let y = 48 + n * PITCH * scale;
        draw_text_scaled(frame, x, y, scale, line);
    }
}

fn show<D: FrameDisplay>(
    display: &mut D,
    frame: &mut Frame,
    lines: &[&str],
) -> Result<(), D::Error> {
    layout(lines, frame);
    display.show(frame)
}

/// What to do when the device has nothing to connect with: join the portal
/// network in your browser. Names the setup button, which is how a user
/// gets back here later without a cable.
pub fn instructions<D: FrameDisplay>(display: &mut D, frame: &mut Frame) -> Result<(), D::Error> {
    show(
        display,
        frame,
        &[
            "Comack setup",
            "",
            "1. Join the Wi-Fi network  comack-setup",
            "2. Open  http://192.168.4.1",
            "3. Enter your Wi-Fi and server, then Save",
            "",
            "To change these later: hold the",
            "LEFT (back) button for 3 seconds.",
        ],
    )
}

/// Acknowledge a saved configuration while the portal closes.
pub fn saved<D: FrameDisplay>(display: &mut D, frame: &mut Frame) -> Result<(), D::Error> {
    show(display, frame, &["Saved!", "", "Joining your Wi-Fi..."])
}

/// Connecting steps: joining Wi-Fi, then reaching the server. Names the
/// network being joined; the tailnet step (when one exists) follows.
pub fn connecting<D: FrameDisplay>(
    display: &mut D,
    frame: &mut Frame,
    ssid: &str,
) -> Result<(), D::Error> {
    show(
        display,
        frame,
        &[
            "Connecting",
            &format!("Wi-Fi: {ssid}"),
            "Looking for the comic server...",
        ],
    )
}

/// Registration is taking too long: the auth key is the likely culprit.
/// The device keeps retrying; the button still gets it back to setup.
pub fn tailscale_stuck<D: FrameDisplay>(
    display: &mut D,
    frame: &mut Frame,
) -> Result<(), D::Error> {
    show(
        display,
        frame,
        &[
            "Tailscale not connecting",
            "",
            "Check the auth key on the server's",
            "admin page, then retry:",
            "hold LEFT (back) 3 s to reconfigure",
        ],
    )
}

/// The server cannot be reached. Names the retry loop and the way out.
pub fn server_unreachable<D: FrameDisplay>(
    display: &mut D,
    frame: &mut Frame,
    host: &str,
    port: u16,
) -> Result<(), D::Error> {
    show(
        display,
        frame,
        &[
            "Can't reach the server",
            &format!("{host}:{port}"),
            "",
            "Check the server is running,",
            "then this screen clears itself.",
            "Reconfigure: hold LEFT (back) 3 s",
        ],
    )
}
