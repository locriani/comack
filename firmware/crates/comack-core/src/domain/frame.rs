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

/// Bytes per colour image: 4 bits per pixel, exactly 192 000.
pub const COLOR_FRAME_BYTES: usize = FRAME_PIXELS / 2;

/// A colour image in the Spectra 6 panel's native packing: row-major, two
/// pixels per byte with the even (left) pixel in the high nibble. Nibbles:
/// 0 black, 1 white, 2 yellow, 3 red, 5 blue, 6 green. Served by
/// `/api/comics/{n}/color.bin`.
pub type ColorFrame = [u8; COLOR_FRAME_BYTES];

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
        assert_eq!(COLOR_FRAME_BYTES, 192_000);
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
