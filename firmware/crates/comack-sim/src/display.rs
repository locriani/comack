//! A display that writes each frame to a PBM image file.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use comack_core::application::ports::FrameDisplay;
use comack_core::domain::{Frame, FRAME_HEIGHT, FRAME_WIDTH};

/// Writes every shown frame to `path` as a binary PBM (`P4`), which any
/// image viewer opens. The file is replaced atomically.
#[derive(Clone, Debug)]
pub struct PbmDisplay {
    path: PathBuf,
    refreshes: u32,
}

impl PbmDisplay {
    /// Show frames in the file at `path`.
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self {
            path: path.into(),
            refreshes: 0,
        }
    }

    /// The output file.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Frames shown so far.
    pub const fn refreshes(&self) -> u32 {
        self.refreshes
    }

    /// Encode a frame as PBM. PBM uses 1 for black, the opposite of frames.
    pub fn encode(frame: &Frame) -> Vec<u8> {
        let mut out = format!("P4\n{FRAME_WIDTH} {FRAME_HEIGHT}\n").into_bytes();
        out.extend(frame.iter().map(|b| !b));
        out
    }
}

impl FrameDisplay for PbmDisplay {
    type Error = io::Error;

    fn show(&mut self, frame: &Frame) -> io::Result<()> {
        let tmp = self.path.with_extension("pbm.tmp");
        fs::write(&tmp, Self::encode(frame))?;
        fs::rename(&tmp, &self.path)?;
        self.refreshes += 1;
        log::info!(
            "[display] frame {} written to {}",
            self.refreshes,
            self.path.display()
        );
        Ok(())
    }
}

#[cfg(test)]
#[allow(clippy::large_stack_arrays)]
mod tests {
    use super::*;
    use comack_core::domain::FRAME_BYTES;

    #[test]
    fn encodes_header_and_inverted_pixels() {
        let mut frame = [0xFF; FRAME_BYTES];
        frame[0] = 0x0F;
        let pbm = PbmDisplay::encode(&frame);
        let header = b"P4\n800 480\n";
        assert_eq!(&pbm[..header.len()], header);
        assert_eq!(pbm.len(), header.len() + FRAME_BYTES);
        assert_eq!(pbm[header.len()], 0xF0);
        assert_eq!(pbm[header.len() + 1], 0x00);
    }

    #[test]
    fn writes_the_file() {
        let dir = std::env::temp_dir().join(format!("comack-sim-pbm-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let mut display = PbmDisplay::new(dir.join("screen.pbm"));
        let frame = [0xAA; FRAME_BYTES];
        display.show(&frame).unwrap();
        assert_eq!(
            fs::read(display.path()).unwrap(),
            PbmDisplay::encode(&frame)
        );
        assert_eq!(display.refreshes(), 1);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn reports_unwritable_paths() {
        let mut display = PbmDisplay::new("/nonexistent-dir/screen.pbm");
        assert!(display.show(&[0; FRAME_BYTES]).is_err());
    }
}
