//! Use case: stream the room's audio to the server, reconnecting as needed.

use core::time::Duration;

use log::{info, warn};

use super::ports::{AudioSession, AudioSource, AudioUplink};
use crate::domain::StreamStats;

/// Timing of the streaming loop.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct StreamerConfig {
    /// Pause between chunks; the microphone read itself paces the loop.
    pub idle_delay: Duration,
    /// Wait this long after a failed connection attempt.
    pub reconnect_delay: Duration,
    /// Log a progress report every this many chunks.
    pub report_every: u32,
}

impl Default for StreamerConfig {
    fn default() -> Self {
        Self {
            idle_delay: Duration::from_millis(10),
            reconnect_delay: Duration::from_secs(2),
            report_every: 50,
        }
    }
}

/// The audio streamer. Call [`AudioStreamer::tick`] in a loop.
pub struct AudioStreamer<'b, M, U: AudioUplink> {
    microphone: M,
    uplink: U,
    session: Option<U::Session>,
    buffer: &'b mut [i16],
    stats: StreamStats,
    config: StreamerConfig,
}

impl<'b, M, U> AudioStreamer<'b, M, U>
where
    M: AudioSource,
    U: AudioUplink,
{
    /// `buffer` sets the chunk size (1600 samples = 100 ms on the device).
    pub fn new(microphone: M, uplink: U, buffer: &'b mut [i16], config: StreamerConfig) -> Self {
        Self {
            microphone,
            uplink,
            session: None,
            buffer,
            stats: StreamStats::new(config.report_every),
            config,
        }
    }

    /// Run one step; returns how long to wait before the next.
    pub fn tick(&mut self) -> Duration {
        let session = match &mut self.session {
            Some(session) => session,
            None => match self.uplink.open() {
                Ok(session) => {
                    info!("Audio stream connected.");
                    self.session.insert(session)
                }
                Err(e) => {
                    warn!("Audio stream connection failed: {e:?}");
                    return self.config.reconnect_delay;
                }
            },
        };

        let samples = match self.microphone.read(self.buffer) {
            Ok(0) => return self.config.idle_delay,
            Ok(n) => &self.buffer[..n.min(self.buffer.len())],
            Err(e) => {
                warn!("Microphone read failed: {e:?}");
                return self.config.idle_delay;
            }
        };

        self.stats.observe(samples);
        match session.send(samples) {
            Ok(()) => {
                if let Some(report) = self.stats.record_sent() {
                    info!(
                        "Audio: {} chunks sent, peak={}",
                        report.chunks_sent, report.peak
                    );
                }
            }
            Err(e) => {
                warn!("Audio stream dropped ({e:?}); reconnecting.");
                self.session = None;
            }
        }
        self.config.idle_delay
    }

    /// Whether a session is currently open.
    pub fn is_connected(&self) -> bool {
        self.session.is_some()
    }

    /// Chunks delivered so far.
    pub fn chunks_sent(&self) -> u32 {
        self.stats.chunks_sent()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::VecDeque;
    use std::rc::Rc;
    use std::vec::Vec;

    #[derive(Debug, PartialEq)]
    struct Fault;

    /// Plays back scripted reads.
    struct Mic(VecDeque<Result<Vec<i16>, Fault>>);

    impl AudioSource for Mic {
        type Error = Fault;

        fn read(&mut self, buffer: &mut [i16]) -> Result<usize, Fault> {
            let block = self.0.pop_front().unwrap_or_else(|| Ok(Vec::new()))?;
            buffer[..block.len()].copy_from_slice(&block);
            Ok(block.len())
        }
    }

    #[derive(Default)]
    struct Server {
        open_results: VecDeque<bool>,
        opens: u32,
        /// Chunks received, tagged with the session that received them.
        received: Vec<(u32, Vec<i16>)>,
        /// Fail the send of the chunk with this global index.
        fail_send_at: Option<usize>,
        sends: usize,
    }

    #[derive(Clone, Default)]
    struct Uplink(Rc<RefCell<Server>>);

    struct Session {
        id: u32,
        server: Rc<RefCell<Server>>,
    }

    impl AudioUplink for Uplink {
        type Session = Session;
        type Error = Fault;

        fn open(&mut self) -> Result<Session, Fault> {
            let mut s = self.0.borrow_mut();
            if !s.open_results.pop_front().unwrap_or(true) {
                return Err(Fault);
            }
            s.opens += 1;
            Ok(Session {
                id: s.opens,
                server: self.0.clone(),
            })
        }
    }

    impl AudioSession for Session {
        type Error = Fault;

        fn send(&mut self, pcm: &[i16]) -> Result<(), Fault> {
            let mut s = self.server.borrow_mut();
            let index = s.sends;
            s.sends += 1;
            if s.fail_send_at == Some(index) {
                return Err(Fault);
            }
            s.received.push((self.id, pcm.to_vec()));
            Ok(())
        }
    }

    fn mic(blocks: &[&[i16]]) -> Mic {
        Mic(blocks.iter().map(|b| Ok(b.to_vec())).collect())
    }

    fn config() -> StreamerConfig {
        StreamerConfig {
            report_every: 2,
            ..StreamerConfig::default()
        }
    }

    #[test]
    fn connects_then_streams_chunks() {
        let uplink = Uplink::default();
        let mut buf = [0i16; 4];
        let mut streamer = AudioStreamer::new(
            mic(&[&[1, 2, 3, 4], &[5, 6]]),
            uplink.clone(),
            &mut buf,
            config(),
        );
        assert_eq!(streamer.tick(), Duration::from_millis(10));
        assert_eq!(streamer.tick(), Duration::from_millis(10));
        assert!(streamer.is_connected());
        assert_eq!(streamer.chunks_sent(), 2);
        let s = uplink.0.borrow();
        assert_eq!(s.opens, 1);
        assert_eq!(
            s.received,
            [(1, std::vec![1, 2, 3, 4]), (1, std::vec![5, 6])]
        );
    }

    #[test]
    fn backs_off_when_connection_fails() {
        let uplink = Uplink::default();
        uplink.0.borrow_mut().open_results.extend([false, false]);
        let mut buf = [0i16; 2];
        let mut streamer = AudioStreamer::new(mic(&[&[9, 9]]), uplink.clone(), &mut buf, config());
        assert_eq!(streamer.tick(), Duration::from_secs(2));
        assert_eq!(streamer.tick(), Duration::from_secs(2));
        assert!(!streamer.is_connected());
        assert_eq!(streamer.tick(), Duration::from_millis(10));
        assert_eq!(uplink.0.borrow().received, [(1, std::vec![9, 9])]);
    }

    #[test]
    fn reconnects_after_a_dropped_session() {
        let uplink = Uplink::default();
        uplink.0.borrow_mut().fail_send_at = Some(1);
        let mut buf = [0i16; 1];
        let mut streamer =
            AudioStreamer::new(mic(&[&[1], &[2], &[3]]), uplink.clone(), &mut buf, config());
        streamer.tick();
        streamer.tick();
        assert!(!streamer.is_connected(), "send failure closes the session");
        streamer.tick();
        let s = uplink.0.borrow();
        assert_eq!(s.opens, 2);
        assert_eq!(s.received, [(1, std::vec![1]), (2, std::vec![3])]);
    }

    #[test]
    fn empty_and_failed_reads_send_nothing() {
        let uplink = Uplink::default();
        let mut buf = [0i16; 2];
        let microphone = Mic(VecDeque::from([
            Ok(Vec::new()),
            Err(Fault),
            Ok(std::vec![4]),
        ]));
        let mut streamer = AudioStreamer::new(microphone, uplink.clone(), &mut buf, config());
        streamer.tick();
        streamer.tick();
        assert_eq!(uplink.0.borrow().received, []);
        assert!(
            streamer.is_connected(),
            "a mic hiccup does not drop the session"
        );
        streamer.tick();
        assert_eq!(uplink.0.borrow().received, [(1, std::vec![4])]);
    }
}
