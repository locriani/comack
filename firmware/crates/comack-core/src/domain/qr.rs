//! Wi-Fi join QR codes drawn into the 1-bit [`Frame`], so a phone camera
//! joins the setup network without typing the password.

use core::fmt::Write;

use heapless::String;
use qrcodegen_no_heap::{QrCode, QrCodeEcc, Version};

use super::frame::{Frame, FRAME_HEIGHT, FRAME_WIDTH};

/// Largest version we allow: a 40-character join payload fits version 3 at
/// medium error correction, so 6 leaves slack for longer SSIDs.
const MAX_VERSION: Version = Version::new(6);

/// Scratch for the encoder: one buffer for the code, one for its working
/// copy, each sized for [`MAX_VERSION`].
const BUFFER_LEN: usize = MAX_VERSION.buffer_len();

/// Longest join payload we build (`WIFI:T:WPA;S:…;P:…;;`).
const PAYLOAD_MAX: usize = 96;

/// The text a phone's camera understands as "join this WPA network".
/// `ssid` and `password` must not contain `\ ; , : "` — ours never do (the
/// SSID is a constant and the password alphabet is alphanumeric).
///
/// Returns `None` if the payload outgrows [`PAYLOAD_MAX`].
pub fn wifi_join_payload(ssid: &str, password: &str) -> Option<String<PAYLOAD_MAX>> {
    let mut payload = String::new();
    write!(payload, "WIFI:T:WPA;S:{ssid};P:{password};;").ok()?;
    Some(payload)
}

/// Draws `text` as a QR code with its top-left corner at (`x`, `y`), each
/// module `module_px` pixels square. Returns the side length in pixels, or
/// `None` when the text does not fit a QR code of up to version 6 or the
/// code would run off the frame. The quiet zone is the caller's: leave white
/// space around the rectangle.
pub fn draw_qr(
    frame: &mut Frame,
    x: usize,
    y: usize,
    module_px: usize,
    text: &str,
) -> Option<usize> {
    let mut code = [0u8; BUFFER_LEN];
    let mut scratch = [0u8; BUFFER_LEN];
    let qr = QrCode::encode_text(
        text,
        &mut scratch,
        &mut code,
        QrCodeEcc::Medium,
        Version::MIN,
        MAX_VERSION,
        None,
        true,
    )
    .ok()?;

    let modules = usize::try_from(qr.size()).ok()?;
    let side = modules * module_px;
    if x + side > FRAME_WIDTH || y + side > FRAME_HEIGHT {
        return None;
    }
    for my in 0..modules {
        for mx in 0..modules {
            if !qr.get_module(i32::try_from(mx).ok()?, i32::try_from(my).ok()?) {
                continue;
            }
            for dy in 0..module_px {
                for dx in 0..module_px {
                    let (px, py) = (x + mx * module_px + dx, y + my * module_px + dy);
                    let bit = py * FRAME_WIDTH + px;
                    frame[bit / 8] &= !(0x80 >> (bit % 8));
                }
            }
        }
    }
    Some(side)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::frame::{black_pixel_count, FRAME_BYTES};

    #[test]
    fn payload_is_the_wifi_join_format() {
        let payload = wifi_join_payload("comack-setup", "AB23CD45EF").unwrap();
        assert_eq!(payload.as_str(), "WIFI:T:WPA;S:comack-setup;P:AB23CD45EF;;");
    }

    #[test]
    fn oversized_payload_is_refused() {
        assert!(wifi_join_payload(&"s".repeat(100), "pw").is_none());
    }

    #[test]
    fn draws_a_square_with_finder_corners() {
        let mut frame: Frame = [0xFF; FRAME_BYTES];
        let text = wifi_join_payload("comack-setup", "AB23CD45EF").unwrap();
        let side = draw_qr(&mut frame, 10, 20, 4, &text).unwrap();
        // Version 3 at medium: 29 modules.
        assert_eq!(side % 4, 0);
        assert!(side >= 21 * 4);
        // Top-left finder pattern corner module is dark.
        let bit = 20 * FRAME_WIDTH + 10;
        assert_eq!(frame[bit / 8] & (0x80 >> (bit % 8)), 0);
        // One pixel outside the code stays white.
        let outside = 20 * FRAME_WIDTH + 9;
        assert_ne!(frame[outside / 8] & (0x80 >> (outside % 8)), 0);
        assert!(black_pixel_count(&frame) > 0);
    }

    #[test]
    fn refuses_to_run_off_the_frame() {
        let mut frame: Frame = [0xFF; FRAME_BYTES];
        assert!(draw_qr(&mut frame, FRAME_WIDTH - 5, 0, 4, "x").is_none());
        assert_eq!(black_pixel_count(&frame), 0);
    }
}
