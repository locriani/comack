//! Microphone level bookkeeping for the audio stream.

/// Largest absolute sample value seen since the last [`PeakMeter::take`].
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct PeakMeter {
    peak: u16,
}

impl PeakMeter {
    /// Fold a block of samples into the peak. `i16::MIN` counts as 32768.
    pub fn observe(&mut self, samples: &[i16]) {
        let block_peak = samples.iter().map(|s| s.unsigned_abs()).max().unwrap_or(0);
        self.peak = self.peak.max(block_peak);
    }

    /// The current peak, without resetting it.
    pub const fn peak(&self) -> u16 {
        self.peak
    }

    /// Return the peak and start a new measurement window.
    pub fn take(&mut self) -> u16 {
        core::mem::take(&mut self.peak)
    }
}

/// A periodic progress report for the audio stream.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct StreamReport {
    /// Chunks delivered since streaming started.
    pub chunks_sent: u32,
    /// Peak level over the reporting window.
    pub peak: u16,
}

/// Counts delivered chunks and produces a [`StreamReport`] every
/// `report_every` of them (50 chunks of 100 ms, about 5 s, on the device).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct StreamStats {
    chunks_sent: u32,
    report_every: u32,
    meter: PeakMeter,
}

impl StreamStats {
    /// `report_every` of 0 is treated as 1.
    pub const fn new(report_every: u32) -> Self {
        Self {
            chunks_sent: 0,
            report_every: if report_every == 0 { 1 } else { report_every },
            meter: PeakMeter { peak: 0 },
        }
    }

    /// Account for captured samples, whether or not they get delivered.
    pub fn observe(&mut self, samples: &[i16]) {
        self.meter.observe(samples);
    }

    /// Account for one delivered chunk; returns a report when one is due.
    pub fn record_sent(&mut self) -> Option<StreamReport> {
        self.chunks_sent = self.chunks_sent.wrapping_add(1);
        (self.chunks_sent % self.report_every == 0).then(|| StreamReport {
            chunks_sent: self.chunks_sent,
            peak: self.meter.take(),
        })
    }

    /// Chunks delivered so far.
    pub const fn chunks_sent(&self) -> u32 {
        self.chunks_sent
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn peak_tracks_largest_magnitude() {
        let mut meter = PeakMeter::default();
        meter.observe(&[10, -300, 200]);
        assert_eq!(meter.peak(), 300);
        meter.observe(&[5]);
        assert_eq!(meter.peak(), 300, "peak holds until taken");
        assert_eq!(meter.take(), 300);
        assert_eq!(meter.peak(), 0);
    }

    #[test]
    fn full_scale_negative_sample_does_not_overflow() {
        let mut meter = PeakMeter::default();
        meter.observe(&[i16::MIN]);
        assert_eq!(meter.peak(), 32_768);
    }

    #[test]
    fn empty_block_leaves_peak_unchanged() {
        let mut meter = PeakMeter::default();
        meter.observe(&[]);
        assert_eq!(meter.peak(), 0);
    }

    #[test]
    fn reports_every_n_chunks_and_resets_peak() {
        let mut stats = StreamStats::new(3);
        stats.observe(&[100]);
        assert_eq!(stats.record_sent(), None);
        stats.observe(&[-400]);
        assert_eq!(stats.record_sent(), None);
        assert_eq!(
            stats.record_sent(),
            Some(StreamReport {
                chunks_sent: 3,
                peak: 400
            })
        );
        stats.observe(&[7]);
        assert_eq!(stats.record_sent(), None);
        assert_eq!(stats.record_sent(), None);
        assert_eq!(
            stats.record_sent(),
            Some(StreamReport {
                chunks_sent: 6,
                peak: 7
            })
        );
        assert_eq!(stats.chunks_sent(), 6);
    }

    #[test]
    fn zero_interval_reports_every_chunk() {
        let mut stats = StreamStats::new(0);
        assert!(stats.record_sent().is_some());
        assert!(stats.record_sent().is_some());
    }

    proptest! {
        #[test]
        fn peak_is_max_unsigned_abs(samples in proptest::collection::vec(any::<i16>(), 0..64)) {
            let mut meter = PeakMeter::default();
            meter.observe(&samples);
            let expected = samples.iter().map(|s| i32::from(*s).unsigned_abs()).max().unwrap_or(0);
            prop_assert_eq!(u32::from(meter.peak()), expected);
        }
    }
}
