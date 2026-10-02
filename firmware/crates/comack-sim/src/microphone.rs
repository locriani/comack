//! A synthetic microphone: bursts of voice-like tones over room noise.

// Signal synthesis: sample counts and amplitudes are small and in range.
#![allow(
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    clippy::cast_precision_loss
)]

use std::convert::Infallible;
use std::time::Duration;

use comack_core::application::ports::AudioSource;

/// Generates 16 kHz PCM: `speech` of a three-formant tone, then `silence`
/// of low noise, repeating. With `realtime` set, reads take as long as the
/// audio they return, like the PDM microphone's blocking reads.
#[derive(Clone, Debug)]
pub struct SyntheticMicrophone {
    sample_rate: u32,
    speech: u64,
    period: u64,
    position: u64,
    noise: u32,
    realtime: bool,
}

impl SyntheticMicrophone {
    /// `speech` then `silence`, repeating.
    pub fn new(sample_rate: u32, speech: Duration, silence: Duration, realtime: bool) -> Self {
        let samples = |d: Duration| (d.as_secs_f64() * f64::from(sample_rate)) as u64;
        let speech = samples(speech);
        Self {
            sample_rate,
            speech,
            period: (speech + samples(silence)).max(1),
            position: 0,
            noise: 0x1234_5678,
            realtime,
        }
    }

    /// Whether the next sample falls in a speech burst.
    pub fn is_speaking(&self) -> bool {
        self.position % self.period < self.speech
    }

    fn next_sample(&mut self) -> i16 {
        let value = if self.is_speaking() {
            let t = self.position as f64 / f64::from(self.sample_rate);
            let tau = std::f64::consts::TAU;
            ((tau * 220.0 * t).sin() * 0.4
                + (tau * 700.0 * t).sin() * 0.3
                + (tau * 2400.0 * t).sin() * 0.15)
                * 12_000.0
        } else {
            // xorshift32 noise, about ±150.
            self.noise ^= self.noise << 13;
            self.noise ^= self.noise >> 17;
            self.noise ^= self.noise << 5;
            f64::from(self.noise % 301) - 150.0
        };
        self.position += 1;
        value as i16
    }
}

impl AudioSource for SyntheticMicrophone {
    type Error = Infallible;

    fn read(&mut self, buffer: &mut [i16]) -> Result<usize, Infallible> {
        for sample in buffer.iter_mut() {
            *sample = self.next_sample();
        }
        if self.realtime {
            std::thread::sleep(Duration::from_secs_f64(
                buffer.len() as f64 / f64::from(self.sample_rate),
            ));
        }
        Ok(buffer.len())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn peak(samples: &[i16]) -> u16 {
        samples.iter().map(|s| s.unsigned_abs()).max().unwrap()
    }

    #[test]
    fn alternates_loud_speech_and_quiet_noise() {
        let mut mic = SyntheticMicrophone::new(
            16_000,
            Duration::from_millis(100),
            Duration::from_millis(100),
            false,
        );
        let mut buf = [0i16; 1600];
        assert!(mic.is_speaking());
        assert_eq!(mic.read(&mut buf), Ok(1600));
        assert!(peak(&buf) > 5_000, "speech is loud");
        assert!(!mic.is_speaking());
        mic.read(&mut buf).unwrap();
        assert!(peak(&buf) <= 150, "silence is quiet");
        assert!(mic.is_speaking(), "cycle repeats");
    }

    #[test]
    fn deterministic() {
        let mut a = SyntheticMicrophone::new(16_000, Duration::ZERO, Duration::from_secs(1), false);
        let mut b = a.clone();
        let (mut x, mut y) = ([0i16; 64], [0i16; 64]);
        a.read(&mut x).unwrap();
        b.read(&mut y).unwrap();
        assert_eq!(x, y);
    }

    #[test]
    fn realtime_reads_take_the_audio_duration() {
        let mut mic =
            SyntheticMicrophone::new(16_000, Duration::ZERO, Duration::from_secs(1), true);
        let mut buf = [0i16; 320];
        let start = std::time::Instant::now();
        mic.read(&mut buf).unwrap();
        assert!(start.elapsed() >= Duration::from_millis(20));
    }
}
