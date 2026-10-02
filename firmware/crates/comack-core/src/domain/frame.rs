//! The 1-bit e-paper frame served by `/api/comics/{n}/bin`.

/// Panel width in pixels.
pub const FRAME_WIDTH: usize = 800;
/// Panel height in pixels.
pub const FRAME_HEIGHT: usize = 480;
/// Pixels per frame.
pub const FRAME_PIXELS: usize = FRAME_WIDTH * FRAME_HEIGHT;
/// Bytes per frame: one bit per pixel, exactly 48 000.
pub const FRAME_BYTES: usize = FRAME_PIXELS / 8;

/// A packed 1-bit-per-pixel frame, row-major, most significant bit first,
/// where a set bit is a white pixel.
pub type Frame = [u8; FRAME_BYTES];

/// Number of black (cleared) pixels in a packed frame.
pub fn black_pixel_count(frame: &[u8]) -> u32 {
    frame.iter().map(|b| (!b).count_ones()).sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_geometry() {
        assert_eq!(FRAME_BYTES, 48_000);
        assert_eq!(FRAME_PIXELS, 384_000);
    }

    #[test]
    fn counts_black_pixels() {
        assert_eq!(black_pixel_count(&[0xFF; 4]), 0);
        assert_eq!(black_pixel_count(&[0x00; 4]), 32);
        assert_eq!(black_pixel_count(&[0b1010_1010, 0b1111_0000]), 8);
        assert_eq!(black_pixel_count(&[]), 0);
    }

    #[test]
    fn all_black_frame_counts_every_pixel() {
        let frame: Frame = [0; FRAME_BYTES];
        assert_eq!(black_pixel_count(&frame) as usize, FRAME_PIXELS);
    }
}
