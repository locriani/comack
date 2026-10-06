//! Device-wide constants shared by every target.

use core::time::Duration;

/// Where the Comack server listens.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ServerEndpoint<'a> {
    /// Tailscale MagicDNS name (or a literal IPv4 address).
    pub host: &'a str,
    /// TCP port of the HTTP/WebSocket API.
    pub port: u16,
}

/// The comack-server running the Comack Python server.
pub const SERVER: ServerEndpoint<'static> = ServerEndpoint {
    host: "comack-server",
    port: 8000,
};

/// Hostname this device registers on the tailnet.
pub const DEVICE_NAME: &str = "comack-display";

/// Microphone sample rate, in Hz (16-bit signed mono PCM).
pub const AUDIO_SAMPLE_RATE_HZ: u32 = 16_000;

/// Samples per streamed chunk: 100 ms at 16 kHz (3200 bytes).
pub const AUDIO_CHUNK_SAMPLES: usize = 1_600;

/// Duration of one audio chunk.
pub const AUDIO_CHUNK_DURATION: Duration = Duration::from_millis(100);

/// Path of the audio WebSocket on the server.
pub const AUDIO_WS_PATH: &str = "/ws/audio";
