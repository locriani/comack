//! Formatting into fixed-size buffers, for building requests without `alloc`.

use core::fmt;

/// A `core::fmt::Write` sink over a byte slice that fails instead of
/// truncating when the slice is full.
pub struct SliceWriter<'a> {
    buf: &'a mut [u8],
    len: usize,
}

impl<'a> SliceWriter<'a> {
    /// Write into `buf` from the start.
    pub fn new(buf: &'a mut [u8]) -> Self {
        Self { buf, len: 0 }
    }

    /// Bytes written so far.
    pub const fn len(&self) -> usize {
        self.len
    }

    /// Whether nothing has been written.
    pub const fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// The bytes written so far.
    pub fn written(&self) -> &[u8] {
        &self.buf[..self.len]
    }
}

impl fmt::Write for SliceWriter<'_> {
    fn write_str(&mut self, s: &str) -> fmt::Result {
        let end = self.len.checked_add(s.len()).ok_or(fmt::Error)?;
        let dst = self.buf.get_mut(self.len..end).ok_or(fmt::Error)?;
        dst.copy_from_slice(s.as_bytes());
        self.len = end;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use core::fmt::Write;

    #[test]
    fn writes_until_full() {
        let mut buf = [0u8; 8];
        let mut w = SliceWriter::new(&mut buf);
        assert!(w.is_empty());
        write!(w, "ab{}", 12).unwrap();
        assert_eq!(w.written(), b"ab12");
        assert_eq!(w.len(), 4);
        assert!(w.write_str("12345").is_err(), "would overflow");
        assert_eq!(w.written(), b"ab12", "failed write leaves contents intact");
        w.write_str("1234").unwrap();
        assert_eq!(w.written(), b"ab121234");
    }
}
