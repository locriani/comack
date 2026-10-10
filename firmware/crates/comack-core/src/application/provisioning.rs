//! Provisioning the device from the setup portal's form submission.
//!
//! [`build_config`] is the whole validation policy: one bounded
//! [`DeviceConfig`] is built from the portal's fields or every problem is
//! named back to the user — nothing is saved unless the whole submission
//! is valid, so a rejected form can never strand the device with a
//! half-written configuration.

use crate::config;
use crate::domain::device_config::{
    ConfigError, DeviceConfig, AUTH_KEY_MAX_LEN, DEVICE_NAME_MAX_LEN, SERVER_HOST_MAX_LEN,
};
use crate::domain::wifi::{WifiCredentials, PASSWORD_MAX_LEN, SSID_MAX_LEN};

/// The fields the portal's form collects, as the user typed them.
///
/// Borrowed views over the decoded form values; [`build_config`] validates
/// and owns them into a [`DeviceConfig`].
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Submission<'a> {
    /// Network to join; required.
    pub ssid: &'a str,
    /// WPA2 passphrase; empty means an open network.
    pub password: &'a str,
    /// Where the server lives: a hostname or a literal `ip:port`.
    pub server_address: &'a str,
    /// Hostname this device registers under; blank keeps the default.
    pub device_name: &'a str,
    /// Tailscale auth key; blank runs LAN-only without a tailnet.
    pub tailscale_auth_key: &'a str,
}

/// Why a submission was rejected. Every variant is named on the portal's
/// rejection page and on the device's e-paper.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProvisionError {
    /// The SSID is required.
    SsidRequired,
    /// The SSID is longer than [`SSID_MAX_LEN`] allows.
    SsidTooLong,
    /// The password is longer than [`PASSWORD_MAX_LEN`] allows.
    PasswordTooLong,
    /// The server address is required.
    ServerAddressRequired,
    /// The server address is longer than [`SERVER_HOST_MAX_LEN`] allows.
    ServerHostTooLong,
    /// The server address is not `hostname` or `hostname:port`.
    InvalidServerAddress,
    /// The device name is longer than [`DEVICE_NAME_MAX_LEN`] allows.
    DeviceNameTooLong,
    /// The Tailscale auth key is longer than [`AUTH_KEY_MAX_LEN`] allows.
    AuthKeyTooLong,
    /// The Wi-Fi credentials were rejected by the configuration builder,
    /// carrying the reason.
    WifiCredentialsRejected(ConfigError),
}

impl core::fmt::Display for ProvisionError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        let message = match self {
            Self::SsidRequired => "the Wi-Fi network name (SSID) is required",
            Self::SsidTooLong => "the Wi-Fi network name (SSID) is too long",
            Self::PasswordTooLong => "the Wi-Fi password is too long",
            Self::ServerAddressRequired => "the server address is required",
            Self::ServerHostTooLong => "the server address is too long",
            Self::InvalidServerAddress => "the server address must be a hostname or ip:port",
            Self::DeviceNameTooLong => "the device name is too long",
            Self::AuthKeyTooLong => "the Tailscale auth key is too long",
            Self::WifiCredentialsRejected(error) => {
                return write!(f, "the Wi-Fi credentials were rejected: {error}")
            }
        };
        f.write_str(message)
    }
}

impl core::error::Error for ProvisionError {}

/// Validates the submission and builds the configuration it describes.
///
/// The portal's one Wi-Fi network becomes the configuration's primary (and
/// only) network: saving replaces whatever was stored, so re-provisioning
/// through the portal is how the network list is changed on purpose.
/// Leaving the device name blank keeps [`config::DEVICE_NAME`], and leaving
/// the server address portless keeps [`config::SERVER`]'s port.
///
/// # Panics
///
/// Panics if a single network would exceed
/// [`MAX_NETWORKS`](crate::domain::wifi::MAX_NETWORKS). The portal
/// submits exactly one network, so this guards the configuration type
/// against ever shrinking below one, rather than an input condition.
pub fn build_config(submission: &Submission<'_>) -> Result<DeviceConfig, ProvisionError> {
    if submission.ssid.is_empty() {
        return Err(ProvisionError::SsidRequired);
    }
    if submission.ssid.len() > SSID_MAX_LEN {
        return Err(ProvisionError::SsidTooLong);
    }
    if submission.password.len() > PASSWORD_MAX_LEN {
        return Err(ProvisionError::PasswordTooLong);
    }
    // Bounds checked above; a failing constructor still rejects the
    // submission instead of panicking.
    let network = WifiCredentials::new(submission.ssid, submission.password)
        .map_err(ProvisionError::WifiCredentialsRejected)?;

    let (server_host, server_port) = parse_server_address(submission.server_address)?;

    let device_name = if submission.device_name.is_empty() {
        config::DEVICE_NAME
    } else {
        submission.device_name
    };
    if device_name.len() > DEVICE_NAME_MAX_LEN {
        return Err(ProvisionError::DeviceNameTooLong);
    }
    if submission.tailscale_auth_key.len() > AUTH_KEY_MAX_LEN {
        return Err(ProvisionError::AuthKeyTooLong);
    }

    let mut networks = heapless::Vec::new();
    networks
        .push(network)
        .expect("one network always fits MAX_NETWORKS");

    Ok(DeviceConfig {
        networks,
        server_host,
        server_port,
        device_name: bounded(device_name),
        // The portal cannot issue identity — the server's settings page
        // does that (spec D1). A provisioned device therefore presents no
        // identity until its pair is entered (secrets.rs or
        // provision-device.sh), and the server keeps it on the `default`
        // device, exactly like pre-identity firmware.
        device_id: heapless::String::new(),
        device_token: heapless::String::new(),
        tailscale_auth_key: bounded(submission.tailscale_auth_key),
    })
}

/// Splits `hostname` or `hostname:port`, defaulting the port to the
/// compile-time server's.
fn parse_server_address(
    address: &str,
) -> Result<(heapless::String<SERVER_HOST_MAX_LEN>, u16), ProvisionError> {
    if address.is_empty() {
        return Err(ProvisionError::ServerAddressRequired);
    }
    let (host, port) = match address.rsplit_once(':') {
        Some((host, port_text)) => {
            // A second colon means an IPv6 literal or malformed input;
            // this device speaks IPv4 only.
            if host.contains(':') {
                return Err(ProvisionError::InvalidServerAddress);
            }
            let port: u16 = port_text
                .parse()
                .map_err(|_| ProvisionError::InvalidServerAddress)?;
            if port == 0 {
                return Err(ProvisionError::InvalidServerAddress);
            }
            (host, port)
        }
        None => (address, config::SERVER.port),
    };
    if host.is_empty() {
        return Err(ProvisionError::ServerAddressRequired);
    }
    if host.len() > SERVER_HOST_MAX_LEN {
        return Err(ProvisionError::ServerHostTooLong);
    }
    Ok((bounded(host), port))
}

/// Copies `text` into a bounded string. Callers check the bound first, so
/// the copy cannot overflow.
fn bounded<const N: usize>(text: &str) -> heapless::String<N> {
    let mut out = heapless::String::new();
    out.push_str(text).expect("bound checked by the caller");
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::SSID_MAX_LEN;

    /// A submission with every field filled in the form's shape.
    fn submission() -> Submission<'static> {
        Submission {
            ssid: "home",
            password: "hunter2",
            server_address: "yourserver:8000",
            device_name: "kitchen-display",
            tailscale_auth_key: "tskey-auth-sample",
        }
    }

    #[test]
    fn a_valid_submission_builds_the_full_config() {
        let config = build_config(&submission()).unwrap();
        assert_eq!(config.networks.len(), 1);
        assert_eq!(config.networks[0].ssid.as_str(), "home");
        assert_eq!(config.networks[0].password.as_str(), "hunter2");
        assert_eq!(config.server_host.as_str(), "yourserver");
        assert_eq!(config.server_port, 8000);
        assert_eq!(config.device_name.as_str(), "kitchen-display");
        assert_eq!(config.tailscale_auth_key.as_str(), "tskey-auth-sample");
    }

    #[test]
    fn a_literal_ip_with_a_port_is_accepted() {
        let submission = Submission {
            server_address: "192.168.1.20:9000",
            ..submission()
        };
        let config = build_config(&submission).unwrap();
        assert_eq!(config.server_host.as_str(), "192.168.1.20");
        assert_eq!(config.server_port, 9000);
    }

    #[test]
    fn a_portless_address_keeps_the_default_port() {
        let submission = Submission {
            server_address: "yourserver",
            ..submission()
        };
        let config = build_config(&submission).unwrap();
        assert_eq!(config.server_host.as_str(), "yourserver");
        assert_eq!(config.server_port, config::SERVER.port);
    }

    #[test]
    fn a_blank_device_name_keeps_the_default() {
        let submission = Submission {
            device_name: "",
            ..submission()
        };
        let config = build_config(&submission).unwrap();
        assert_eq!(config.device_name.as_str(), config::DEVICE_NAME);
    }

    #[test]
    fn a_blank_tailscale_key_is_lan_only() {
        let submission = Submission {
            tailscale_auth_key: "",
            ..submission()
        };
        let config = build_config(&submission).unwrap();
        assert!(config.tailscale_auth_key.is_empty());
        // Everything else still applies.
        assert_eq!(config.networks[0].ssid.as_str(), "home");
    }

    #[test]
    fn an_open_network_has_an_empty_password() {
        let submission = Submission {
            password: "",
            ..submission()
        };
        let config = build_config(&submission).unwrap();
        assert_eq!(config.networks[0].password.as_str(), "");
    }

    #[test]
    fn an_empty_ssid_is_rejected() {
        let submission = Submission {
            ssid: "",
            ..submission()
        };
        assert_eq!(build_config(&submission), Err(ProvisionError::SsidRequired));
    }

    #[test]
    fn an_empty_server_address_is_rejected() {
        let submission = Submission {
            server_address: "",
            ..submission()
        };
        assert_eq!(
            build_config(&submission),
            Err(ProvisionError::ServerAddressRequired)
        );
    }

    #[test]
    fn over_long_fields_are_rejected_with_a_named_error() {
        let long_ssid = "x".repeat(SSID_MAX_LEN + 1);
        let long_password = "x".repeat(PASSWORD_MAX_LEN + 1);
        let long_host = "x".repeat(SERVER_HOST_MAX_LEN + 1);
        let long_name = "x".repeat(DEVICE_NAME_MAX_LEN + 1);
        let long_key = "x".repeat(AUTH_KEY_MAX_LEN + 1);

        assert_eq!(
            build_config(&Submission {
                ssid: &long_ssid,
                ..submission()
            }),
            Err(ProvisionError::SsidTooLong)
        );
        assert_eq!(
            build_config(&Submission {
                password: &long_password,
                ..submission()
            }),
            Err(ProvisionError::PasswordTooLong)
        );
        assert_eq!(
            build_config(&Submission {
                server_address: &long_host,
                ..submission()
            }),
            Err(ProvisionError::ServerHostTooLong)
        );
        assert_eq!(
            build_config(&Submission {
                device_name: &long_name,
                ..submission()
            }),
            Err(ProvisionError::DeviceNameTooLong)
        );
        assert_eq!(
            build_config(&Submission {
                tailscale_auth_key: &long_key,
                ..submission()
            }),
            Err(ProvisionError::AuthKeyTooLong)
        );
    }

    #[test]
    fn malformed_addresses_are_rejected() {
        for address in [
            "yourserver:",      // port missing
            "yourserver:0",     // port zero
            "yourserver:abc",   // port not a number
            "yourserver:99999", // port over u16
            "::1",              // IPv6 is not supported
            "host:8000:9000",   // too many colons
        ] {
            assert_eq!(
                build_config(&Submission {
                    server_address: address,
                    ..submission()
                }),
                Err(ProvisionError::InvalidServerAddress),
                "'{address}' should be rejected"
            );
        }
    }

    #[test]
    fn a_hostless_port_is_reported_as_a_missing_address() {
        let submission = Submission {
            server_address: ":8000",
            ..submission()
        };
        assert_eq!(
            build_config(&submission),
            Err(ProvisionError::ServerAddressRequired)
        );
    }

    #[test]
    fn a_maximal_ssid_fits() {
        let max_ssid = "x".repeat(SSID_MAX_LEN);
        let submission = Submission {
            ssid: &max_ssid,
            ..submission()
        };
        let config = build_config(&submission).unwrap();
        assert_eq!(config.networks[0].ssid.as_str(), max_ssid);
    }

    #[test]
    fn errors_display_as_user_facing_sentences() {
        assert_eq!(
            ProvisionError::SsidRequired.to_string(),
            "the Wi-Fi network name (SSID) is required"
        );
        assert_eq!(
            ProvisionError::InvalidServerAddress.to_string(),
            "the server address must be a hostname or ip:port"
        );
    }
}
