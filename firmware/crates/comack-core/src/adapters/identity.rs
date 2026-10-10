//! The device identity a client presents on identity-aware requests: the
//! registry pair the server issued (`?device_id=&device_token=`,
//! server/devices.py), shared by the HTTP comic service and the audio
//! WebSocket uplink so the wire format lives in exactly one place.

use core::fmt;

/// The identity a device presents on identity-aware requests: the registry
/// pair the server issued (`?device_id=&device_token=`, server/devices.py).
/// Both values are server-issued and URL-safe — slugs and `token_urlsafe` —
/// so they need no percent-encoding on the wire.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DeviceIdentity<'a> {
    /// Registry id, e.g. `living-room`.
    pub device_id: &'a str,
    /// The per-device token, shown once at registration.
    pub device_token: &'a str,
}

impl DeviceIdentity<'_> {
    /// Presenting nothing: the request lines stay bare, exactly what
    /// tokenless firmware has always sent, and the server binds those
    /// callers to its auto `default` device.
    pub const NONE: Self = Self {
        device_id: "",
        device_token: "",
    };

    /// Whether any identity would be presented. A partial pair (an id
    /// without its token, say) still counts: it goes out as-is and the
    /// server rejects it — never silently downgraded to tokenless.
    pub const fn is_present(&self) -> bool {
        !self.device_id.is_empty() || !self.device_token.is_empty()
    }
}

/// A request path plus the identity it carries: the bare path for a device
/// with no identity, `path?device_id=…&device_token=…` otherwise. Does not
/// allocate; an over-long rendering overflows the request buffer and
/// surfaces as a request-too-large error at request time.
#[derive(Clone, Copy)]
pub(crate) struct IdentityPath<'a> {
    pub base: &'a str,
    pub identity: &'a DeviceIdentity<'a>,
}

impl fmt::Display for IdentityPath<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if !self.identity.is_present() {
            return f.write_str(self.base);
        }
        write!(
            f,
            "{}?device_id={}&device_token={}",
            self.base, self.identity.device_id, self.identity.device_token
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rendered(base: &str, device_id: &str, device_token: &str) -> std::string::String {
        let identity = DeviceIdentity {
            device_id,
            device_token,
        };
        IdentityPath {
            base,
            identity: &identity,
        }
        .to_string()
    }

    #[test]
    fn a_pair_is_appended_as_query_parameters() {
        assert_eq!(
            rendered("/ws/audio", "living-room", "t0ken"),
            "/ws/audio?device_id=living-room&device_token=t0ken"
        );
    }

    #[test]
    fn no_identity_keeps_the_bare_path() {
        assert_eq!(rendered("/ws/audio", "", ""), "/ws/audio");
        assert_eq!(rendered("/api/trigger", "", ""), "/api/trigger");
    }

    #[test]
    fn a_partial_pair_is_rendered_as_presented() {
        // The firmware never strips a broken identity down to tokenless.
        assert_eq!(
            rendered("/ws/audio", "living-room", ""),
            "/ws/audio?device_id=living-room&device_token="
        );
        assert_eq!(
            rendered("/ws/audio", "", "t0ken"),
            "/ws/audio?device_id=&device_token=t0ken"
        );
    }

    #[test]
    fn presence_is_any_presented_field() {
        assert!(!DeviceIdentity::NONE.is_present());
        assert!(DeviceIdentity {
            device_id: "living-room",
            device_token: "t0ken",
        }
        .is_present());
        assert!(DeviceIdentity {
            device_id: "living-room",
            device_token: "",
        }
        .is_present());
        assert!(DeviceIdentity {
            device_id: "",
            device_token: "t0ken",
        }
        .is_present());
    }
}
