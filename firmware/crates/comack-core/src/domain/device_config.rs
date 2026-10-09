//! The device's runtime configuration and its persisted form.
//!
//! One bounded [`DeviceConfig`] — the networks to join, where the server
//! lives, who this device is — plus a versioned binary codec for the config
//! store, so what a provisioning flow saves is exactly what the supervisor
//! loads. Everything is `no_std`: buffers are `heapless`, nothing allocates.

use core::fmt;

use super::wifi::{WifiCredentials, MAX_NETWORKS, PASSWORD_MAX_LEN, SSID_MAX_LEN};
use crate::config::{self, ServerEndpoint};

/// Server host capacity: a MagicDNS name or a dotted-quad address.
pub const SERVER_HOST_MAX_LEN: usize = 128;

/// Device-name capacity.
pub const DEVICE_NAME_MAX_LEN: usize = 32;

/// Tailscale auth-key capacity; `tskey-auth-…` keys are around 65.
pub const AUTH_KEY_MAX_LEN: usize = 80;

/// Why a configuration could not be built from its parts or decoded from
/// its stored bytes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ConfigError {
    /// An SSID is longer than [`SSID_MAX_LEN`].
    SsidTooLong,
    /// A password is longer than [`PASSWORD_MAX_LEN`].
    PasswordTooLong,
    /// The server host is longer than [`SERVER_HOST_MAX_LEN`].
    ServerHostTooLong,
    /// The device name is longer than [`DEVICE_NAME_MAX_LEN`].
    DeviceNameTooLong,
    /// The Tailscale auth key is longer than [`AUTH_KEY_MAX_LEN`].
    AuthKeyTooLong,
    /// The stored bytes do not decode into a configuration.
    Corrupted,
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let message = match self {
            Self::SsidTooLong => "SSID too long",
            Self::PasswordTooLong => "password too long",
            Self::ServerHostTooLong => "server host too long",
            Self::DeviceNameTooLong => "device name too long",
            Self::AuthKeyTooLong => "Tailscale auth key too long",
            Self::Corrupted => "stored configuration is corrupt",
        };
        f.write_str(message)
    }
}

impl core::error::Error for ConfigError {}

/// How the device joins, whom it talks to and who it is. Read from the
/// config store at boot; the compile-time `secrets.rs` values fill in
/// when nothing is stored.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DeviceConfig {
    /// Networks to join, primary first.
    pub networks: heapless::Vec<WifiCredentials, MAX_NETWORKS>,
    /// The Comack server: Tailscale MagicDNS name or a literal IP address.
    pub server_host: heapless::String<SERVER_HOST_MAX_LEN>,
    /// TCP port of the server's HTTP/WebSocket API.
    pub server_port: u16,
    /// Hostname this device registers under.
    pub device_name: heapless::String<DEVICE_NAME_MAX_LEN>,
    /// Tailscale auth key; empty runs without a tailnet.
    pub tailscale_auth_key: heapless::String<AUTH_KEY_MAX_LEN>,
}

impl Default for DeviceConfig {
    /// Port and device name mirror the compile-time constants; everything
    /// else starts empty.
    fn default() -> Self {
        let mut device_name = heapless::String::new();
        device_name
            .push_str(config::DEVICE_NAME)
            .expect("DEVICE_NAME fits the bounded buffer");
        Self {
            networks: heapless::Vec::new(),
            server_host: heapless::String::new(),
            server_port: config::SERVER.port,
            device_name,
            tailscale_auth_key: heapless::String::new(),
        }
    }
}

impl DeviceConfig {
    /// Builds the compile-time fallback: this build's `secrets.rs` values
    /// (networks, Tailscale key) and `config.rs` constants (server,
    /// device name). The config store falls back to it when nothing is
    /// stored, so already-flashed devices keep booting unchanged.
    ///
    /// # Panics
    ///
    /// Only if the caller's network list overflowed [`MAX_NETWORKS`], which
    /// its type makes unrepresentable; the `expect` pins that invariant.
    pub fn from_compile_time(
        networks: &heapless::Vec<WifiCredentials, MAX_NETWORKS>,
        server: ServerEndpoint<'_>,
        device_name: &str,
        tailscale_auth_key: &str,
    ) -> Result<Self, ConfigError> {
        let mut server_host = heapless::String::new();
        server_host
            .push_str(server.host)
            .map_err(|_| ConfigError::ServerHostTooLong)?;
        let mut device_name_buffer = heapless::String::new();
        device_name_buffer
            .push_str(device_name)
            .map_err(|_| ConfigError::DeviceNameTooLong)?;
        let mut auth_key = heapless::String::new();
        auth_key
            .push_str(tailscale_auth_key)
            .map_err(|_| ConfigError::AuthKeyTooLong)?;
        Ok(Self {
            // Same bounded type, so the compile-time list carries over as-is.
            networks: networks.clone(),
            server_host,
            server_port: server.port,
            device_name: device_name_buffer,
            tailscale_auth_key: auth_key,
        })
    }

    /// Serialises the configuration for the config store.
    ///
    /// Cannot fail for over-long fields: every field is bounded by its
    /// capacity and [`MAX_NETWORKS`] caps the network list, exercised at
    /// full size by `encoded_max_fits_a_maximal_config`.
    ///
    /// # Panics
    ///
    /// Only if those bounds and [`ENCODED_MAX`] ever drift apart; the
    /// `expect`s pin the invariant the maximal-config test keeps true.
    pub fn encode(&self) -> heapless::Vec<u8, ENCODED_MAX> {
        let mut out = heapless::Vec::new();
        push_byte(&mut out, CODEC_VERSION);
        push_byte(
            &mut out,
            u8::try_from(self.networks.len()).expect("networks cap is 5"),
        );
        for network in &self.networks {
            push_str(&mut out, network.ssid.as_str());
            push_str(&mut out, network.password.as_str());
        }
        push_str(&mut out, self.server_host.as_str());
        let port_bytes = self.server_port.to_le_bytes();
        push_byte(&mut out, port_bytes[0]);
        push_byte(&mut out, port_bytes[1]);
        push_str(&mut out, self.device_name.as_str());
        push_str(&mut out, self.tailscale_auth_key.as_str());
        out
    }

    /// Parses bytes written by [`DeviceConfig::encode`]. Anything that is
    /// not a whole blob of this codec's own layout — wrong version, cut
    /// short, malformed lengths, trailing bytes — is [`ConfigError::Corrupted`].
    pub fn decode(bytes: &[u8]) -> Result<Self, ConfigError> {
        let mut reader = Reader::new(bytes);
        if reader.u8()? != CODEC_VERSION {
            return Err(ConfigError::Corrupted);
        }
        let count = usize::from(reader.u8()?);
        if count > MAX_NETWORKS {
            return Err(ConfigError::Corrupted);
        }
        let mut networks = heapless::Vec::new();
        for _ in 0..count {
            let ssid = reader.string()?;
            let password = reader.string()?;
            networks
                .push(WifiCredentials { ssid, password })
                .map_err(|_| ConfigError::Corrupted)?;
        }
        let config = Self {
            networks,
            server_host: reader.string()?,
            server_port: u16::from_le_bytes([reader.u8()?, reader.u8()?]),
            device_name: reader.string()?,
            tailscale_auth_key: reader.string()?,
        };
        if !reader.is_empty() {
            return Err(ConfigError::Corrupted);
        }
        Ok(config)
    }
}

/// Codec version; decode rejects any other value rather than guess.
const CODEC_VERSION: u8 = 1;

/// Worst-case encoded size: version and network-count bytes, five fully
/// sized networks, then the length-prefixed host, the port, and the
/// length-prefixed name and key.
pub const ENCODED_MAX: usize = 2
    + MAX_NETWORKS * (1 + SSID_MAX_LEN + 1 + PASSWORD_MAX_LEN)
    + 1
    + SERVER_HOST_MAX_LEN
    + 2
    + 1
    + DEVICE_NAME_MAX_LEN
    + 1
    + AUTH_KEY_MAX_LEN;

fn push_byte(out: &mut heapless::Vec<u8, ENCODED_MAX>, byte: u8) {
    out.push(byte)
        .expect("every byte count here is bounded by ENCODED_MAX");
}

fn push_str(out: &mut heapless::Vec<u8, ENCODED_MAX>, value: &str) {
    let len = u8::try_from(value.len()).expect("field capacities are below 256");
    push_byte(out, len);
    out.extend_from_slice(value.as_bytes())
        .expect("bounded by ENCODED_MAX");
}

/// Cursor over encoded bytes; every read is bounds-checked.
struct Reader<'a> {
    bytes: &'a [u8],
    position: usize,
}

impl<'a> Reader<'a> {
    fn new(bytes: &'a [u8]) -> Self {
        Self { bytes, position: 0 }
    }

    fn u8(&mut self) -> Result<u8, ConfigError> {
        let byte = *self
            .bytes
            .get(self.position)
            .ok_or(ConfigError::Corrupted)?;
        self.position += 1;
        Ok(byte)
    }

    fn string<const N: usize>(&mut self) -> Result<heapless::String<N>, ConfigError> {
        let len = usize::from(self.u8()?);
        let end = self
            .position
            .checked_add(len)
            .ok_or(ConfigError::Corrupted)?;
        let bytes = self
            .bytes
            .get(self.position..end)
            .ok_or(ConfigError::Corrupted)?;
        self.position = end;
        let text = core::str::from_utf8(bytes).map_err(|_| ConfigError::Corrupted)?;
        let mut out = heapless::String::new();
        // Capacity is part of `N`; an over-long field is a corrupt blob.
        out.push_str(text).map_err(|_| ConfigError::Corrupted)?;
        Ok(out)
    }

    fn is_empty(&self) -> bool {
        self.position >= self.bytes.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    /// A configuration with one of everything, built like the device
    /// builds its compile-time fallback.
    fn sample_config() -> DeviceConfig {
        let networks = heapless::Vec::from([
            WifiCredentials::new("home", "pw").unwrap(),
            WifiCredentials::new("phone", "").unwrap(),
        ]);
        DeviceConfig::from_compile_time(
            &networks,
            ServerEndpoint {
                host: "localhost",
                port: 8000,
            },
            "comack-display",
            "tskey-auth-sample",
        )
        .unwrap()
    }

    fn bounded<const N: usize>(text: &str) -> heapless::String<N> {
        let mut out = heapless::String::new();
        out.push_str(text).expect("test string fits the bound");
        out
    }

    #[test]
    fn default_matches_the_compile_time_constants() {
        let config = DeviceConfig::default();
        assert_eq!(config.server_port, config::SERVER.port);
        assert_eq!(config.device_name.as_str(), config::DEVICE_NAME);
        assert!(config.networks.is_empty());
        assert!(config.server_host.is_empty());
        assert!(config.tailscale_auth_key.is_empty());
    }

    #[test]
    fn rejects_over_long_fields() {
        let long_ssid = "x".repeat(SSID_MAX_LEN + 1);
        let long_password = "x".repeat(PASSWORD_MAX_LEN + 1);
        let long_host = "x".repeat(SERVER_HOST_MAX_LEN + 1);
        let long_name = "x".repeat(DEVICE_NAME_MAX_LEN + 1);
        let long_key = "x".repeat(AUTH_KEY_MAX_LEN + 1);
        let server = ServerEndpoint {
            host: "localhost",
            port: 8000,
        };

        assert_eq!(
            WifiCredentials::new(&long_ssid, ""),
            Err(ConfigError::SsidTooLong)
        );
        assert_eq!(
            WifiCredentials::new("ssid", &long_password),
            Err(ConfigError::PasswordTooLong)
        );
        assert_eq!(
            DeviceConfig::from_compile_time(
                &heapless::Vec::new(),
                ServerEndpoint {
                    host: &long_host,
                    port: 8000
                },
                "comack-display",
                ""
            ),
            Err(ConfigError::ServerHostTooLong)
        );
        assert_eq!(
            DeviceConfig::from_compile_time(&heapless::Vec::new(), server, &long_name, ""),
            Err(ConfigError::DeviceNameTooLong)
        );
        assert_eq!(
            DeviceConfig::from_compile_time(
                &heapless::Vec::new(),
                server,
                "comack-display",
                &long_key
            ),
            Err(ConfigError::AuthKeyTooLong)
        );
    }

    #[test]
    fn encoded_max_fits_a_maximal_config() {
        let mut config = DeviceConfig::default();
        for _ in 0..MAX_NETWORKS {
            config
                .networks
                .push(
                    WifiCredentials::new(&"s".repeat(SSID_MAX_LEN), &"p".repeat(PASSWORD_MAX_LEN))
                        .unwrap(),
                )
                .unwrap();
        }
        config.server_host = bounded(&"h".repeat(SERVER_HOST_MAX_LEN));
        config.device_name = bounded(&"n".repeat(DEVICE_NAME_MAX_LEN));
        config.tailscale_auth_key = bounded(&"k".repeat(AUTH_KEY_MAX_LEN));

        let encoded = config.encode();
        assert_eq!(encoded.len(), ENCODED_MAX);
        assert_eq!(DeviceConfig::decode(&encoded).unwrap(), config);
    }

    #[test]
    fn decode_round_trips_a_sample() {
        let config = sample_config();
        assert_eq!(DeviceConfig::decode(&config.encode()).unwrap(), config);
    }

    #[test]
    fn decode_rejects_truncated_bytes() {
        let encoded = sample_config().encode();
        assert_eq!(
            DeviceConfig::decode(&encoded[..encoded.len() - 1]),
            Err(ConfigError::Corrupted)
        );
        assert_eq!(DeviceConfig::decode(&[]), Err(ConfigError::Corrupted));
    }

    #[test]
    fn decode_rejects_an_unknown_version() {
        let mut encoded = sample_config().encode();
        encoded[0] = CODEC_VERSION + 1;
        assert_eq!(DeviceConfig::decode(&encoded), Err(ConfigError::Corrupted));
    }

    #[test]
    fn decode_rejects_an_impossible_network_count() {
        let mut encoded = sample_config().encode();
        encoded[1] = (MAX_NETWORKS + 1) as u8;
        assert_eq!(DeviceConfig::decode(&encoded), Err(ConfigError::Corrupted));
    }

    #[test]
    fn decode_rejects_trailing_bytes() {
        let mut encoded = sample_config().encode();
        encoded.push(0).unwrap();
        assert_eq!(DeviceConfig::decode(&encoded), Err(ConfigError::Corrupted));
    }

    proptest! {
        #[test]
        fn round_trips_any_bounded_config(
            networks in prop::collection::vec(("[ -~]{0,33}", "[ -~]{0,64}"), 0..=5),
            server_host in "[ -~]{0,128}",
            server_port in any::<u16>(),
            device_name in "[ -~]{0,32}",
            auth_key in "[ -~]{0,80}",
        ) {
            let mut config = DeviceConfig::default();
            for (ssid, password) in networks {
                config
                    .networks
                    .push(WifiCredentials::new(&ssid, &password).unwrap())
                    .unwrap();
            }
            config.server_host = bounded(&server_host);
            config.server_port = server_port;
            config.device_name = bounded(&device_name);
            config.tailscale_auth_key = bounded(&auth_key);
            assert_eq!(DeviceConfig::decode(&config.encode()).unwrap(), config);
        }
    }
}
