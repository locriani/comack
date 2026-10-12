//! The provisioning and connection e-paper screens: text on the 800×480
//! panel, drawn with the core's scaled glyph renderer. Every screen writes
//! into a caller-provided frame buffer (PSRAM-backed in the composition
//! root — 48 KB does not belong on a task stack) and shows it once; the
//! panel holds the image until the next refresh.

use comack_core::application::ports::FrameDisplay;
use comack_core::domain::{draw_qr, draw_text_scaled, wifi_join_payload, Frame, FRAME_WIDTH};

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
    frame.fill(0xFF); // not `*frame = [..]`: that builds a 48 KB temporary on the main stack
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

/// Left edge of the instruction text and the QR's position on its right.
const TEXT_X: usize = 30;
const QR_X: usize = 540;
const QR_Y: usize = 130;
/// QR module size in pixels: version 3 (29 modules) spans 203 px.
const QR_MODULE_PX: usize = 7;

/// What to do when the device has nothing to connect with: join the
/// password-protected portal network (scan the QR or type the password) and
/// open the form. Names the setup gesture, which is how a user gets back
/// here later without a cable.
pub fn instructions<D: FrameDisplay>(
    display: &mut D,
    frame: &mut Frame,
    ssid: &str,
    password: &str,
    url: &str,
) -> Result<(), D::Error> {
    frame.fill(0xFF);
    let heading = "Comack setup";
    let heading_x = FRAME_WIDTH - heading.len() * ADVANCE * HEADING_SCALE;
    draw_text_scaled(frame, heading_x / 2, 30, HEADING_SCALE, heading);

    let (ssid_line, password_line, url_line) = (
        format!("   {ssid}"),
        format!("   Password: {password}"),
        format!("   {url}"),
    );
    let lines = [
        "1. Scan the QR code, or",
        "   join Wi-Fi:",
        &ssid_line,
        &password_line,
        "",
        "2. Open",
        &url_line,
        "",
        "3. Fill in the form, Save",
    ];
    for (n, line) in lines.iter().enumerate() {
        draw_text_scaled(
            frame,
            TEXT_X,
            100 + n * PITCH * BODY_SCALE,
            BODY_SCALE,
            line,
        );
    }
    // A missing QR is not fatal: the typed password above still works.
    if let Some(payload) = wifi_join_payload(ssid, password) {
        let _ = draw_qr(frame, QR_X, QR_Y, QR_MODULE_PX, &payload);
    }

    let footer = "Change later: hold all 3 buttons 10 s";
    let footer_x = FRAME_WIDTH - footer.len() * ADVANCE * BODY_SCALE;
    draw_text_scaled(frame, footer_x / 2, 420, BODY_SCALE, footer);
    display.show(frame)
}

/// Acknowledge a saved configuration while the portal closes.
pub fn saved<D: FrameDisplay>(display: &mut D, frame: &mut Frame) -> Result<(), D::Error> {
    show(display, frame, &["Saved!", "", "Joining your Wi-Fi..."])
}

/// Joining the configured Wi-Fi. Names the SSID; failures upgrade to
/// [`wifi_stuck`] after a timeout while the supervisor keeps retrying.
pub fn connecting_wifi<D: FrameDisplay>(
    display: &mut D,
    frame: &mut Frame,
    ssid: &str,
) -> Result<(), D::Error> {
    show(
        display,
        frame,
        &[
            "Joining Wi-Fi",
            &format!("{ssid}"),
            "",
            "2.4 GHz only. Waiting for an IP...",
        ],
    )
}

/// Wi-Fi will not associate. Names the SSID and the way back to setup;
/// the supervisor keeps rotating / retrying underneath.
pub fn wifi_stuck<D: FrameDisplay>(
    display: &mut D,
    frame: &mut Frame,
    ssid: &str,
) -> Result<(), D::Error> {
    show(
        display,
        frame,
        &[
            "Wi-Fi not connecting",
            &format!("{ssid}"),
            "",
            "Check SSID, password, and that the",
            "network is 2.4 GHz (not 5 GHz only).",
            "Hold all 3 buttons 10 s to reconfigure",
        ],
    )
}

/// Tailscale registration has started; the control plane can take a while.
pub fn joining_tailscale<D: FrameDisplay>(
    display: &mut D,
    frame: &mut Frame,
) -> Result<(), D::Error> {
    show(
        display,
        frame,
        &[
            "Joining Tailscale",
            "",
            "Registering this device on the",
            "tailnet. This can take a minute.",
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
            "Check the auth key (tskey-auth-...),",
            "then retry:",
            "hold all 3 buttons 10 s to reconfigure",
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
            "Is the server up? On the right",
            "network / tailnet?",
            "Hold all 3 buttons 10 s to reconfigure",
        ],
    )
}
