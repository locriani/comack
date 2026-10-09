//! The provisioning portal's HTTP surface: the embedded form, the
//! urlencoded decoder, and the one routing decision every server shares.
//!
//! The device's SoftAP server and the simulator's test server both call
//! [`route`], so their wire behavior is identical and testable without a
//! socket.

use heapless::String as HeaplessString;

use crate::adapters::http::Method;
use crate::application::ports::DeviceConfigStore;
use crate::application::provisioning::{build_config, ProvisionError, Submission};

/// Upper bound on one percent-decoded field. Every validated bound is far
/// smaller, so values between a bound and this size decode fully and are
/// rejected with a named message by the use case instead of being silently
/// truncated; only truly runaway input hits the decoder's limit.
const FORM_MAX_BYTES: usize = 256;

/// The setup form compiled into the binary (the repo's no-build
/// convention). Served on the SoftAP network while provisioning.
pub const FORM_HTML: &str = include_str!("portal_form.html");

/// Wraps a body fragment in the portal's page shell.
macro_rules! page {
    ($body:literal) => {
        concat!(
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            "<meta name='viewport' content='width=device-width,initial-scale=1'>",
            "<title>Comack setup</title>",
            "<style>body{font-family:sans-serif;margin:2rem;max-width:26rem}",
            "a{color:#06c}code{background:#eee;padding:0 .2rem}</style></head><body>",
            $body,
            "</body></html>"
        )
    };
}

/// Shown after the configuration was saved; the device then leaves portal
/// mode and reconnects.
pub const SAVED_HTML: &str = page!(
    "<h1>Saved</h1><p>Configuration saved. The device is leaving setup mode \
     and joining your network now &mdash; this page will stop responding \
     shortly.</p>"
);

/// Shown for a request that is neither the form nor a submission.
pub const NOT_FOUND_HTML: &str = page!(
    "<h1>Not found</h1><p>This page does not exist. \
     <a href='/'>Open the setup form</a>.</p>"
);

/// Shown when the submitted body could not be decoded as a form.
pub const DECODE_FAILED_HTML: &str = page!(
    "<h1>Couldn't read the form</h1><p>The submission could not be decoded. \
     <a href='/'>Try again</a>.</p>"
);

/// Shown when the configuration store rejected the save.
pub const SAVE_FAILED_HTML: &str = page!(
    "<h1>Couldn't save</h1><p>Saving the configuration failed on the device. \
     <a href='/'>Try again</a>.</p>"
);

/// Static rejection page for one [`ProvisionError`].
macro_rules! rejection {
    ($name:ident, $message:literal) => {
        /// Rejection page naming the reason; see the message.
        pub const $name: &str = page!($message);
    };
}

rejection!(
    REJECT_SSID_REQUIRED,
    "<h1>Couldn't save</h1><p>The Wi-Fi network name (SSID) is required. \
     <a href='/'>Try again</a>.</p>"
);
rejection!(
    REJECT_SSID_TOO_LONG,
    "<h1>Couldn't save</h1><p>The Wi-Fi network name (SSID) is too long. \
     <a href='/'>Try again</a>.</p>"
);
rejection!(
    REJECT_PASSWORD_TOO_LONG,
    "<h1>Couldn't save</h1><p>The Wi-Fi password is too long. \
     <a href='/'>Try again</a>.</p>"
);
rejection!(
    REJECT_SERVER_REQUIRED,
    "<h1>Couldn't save</h1><p>The server address is required. \
     <a href='/'>Try again</a>.</p>"
);
rejection!(
    REJECT_SERVER_TOO_LONG,
    "<h1>Couldn't save</h1><p>The server address is too long. \
     <a href='/'>Try again</a>.</p>"
);
rejection!(
    REJECT_SERVER_INVALID,
    "<h1>Couldn't save</h1><p>The server address must be a hostname or an \
     ip:port, for example <code>your-server:8000</code> or \
     <code>192.168.1.20:8000</code>. <a href='/'>Try again</a>.</p>"
);
rejection!(
    REJECT_NAME_TOO_LONG,
    "<h1>Couldn't save</h1><p>The device name is too long. \
     <a href='/'>Try again</a>.</p>"
);
rejection!(
    REJECT_KEY_TOO_LONG,
    "<h1>Couldn't save</h1><p>The Tailscale auth key is too long. \
     <a href='/'>Try again</a>.</p>"
);
rejection!(
    REJECT_WIFI_CREDENTIALS,
    "<h1>Couldn't save</h1><p>The Wi-Fi credentials were rejected. Check the \
     network name and password lengths and <a href='/'>try again</a>.</p>"
);

/// One decoded form submission. Fields are bounded by [`FORM_MAX_BYTES`]
/// and validated by the use case, not here.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct FormFields {
    /// SSID, decoded.
    pub ssid: HeaplessString<FORM_MAX_BYTES>,
    /// Password, decoded.
    pub password: HeaplessString<FORM_MAX_BYTES>,
    /// Server address (`host` or `host:port`), decoded.
    pub server: HeaplessString<FORM_MAX_BYTES>,
    /// Device name, decoded.
    pub device_name: HeaplessString<FORM_MAX_BYTES>,
    /// Tailscale auth key, decoded.
    pub tailscale_key: HeaplessString<FORM_MAX_BYTES>,
}

impl FormFields {
    /// Borrows the decoded fields as a [`Submission`] for validation.
    pub fn submission(&self) -> Submission<'_> {
        Submission {
            ssid: &self.ssid,
            password: &self.password,
            server_address: &self.server,
            device_name: &self.device_name,
            tailscale_auth_key: &self.tailscale_key,
        }
    }
}

/// The body could not be decoded: malformed escapes, invalid UTF-8, or a
/// field over [`FORM_MAX_BYTES`].
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FormDecodeError;

/// Decodes an `application/x-www-form-urlencoded` body.
///
/// Unknown keys are ignored (forward-compatible), `+` means space, and
/// `%XX` escapes are byte-decoded before the value is checked as UTF-8,
/// so multi-byte characters survive.
pub fn decode_form(body: &[u8]) -> Result<FormFields, FormDecodeError> {
    let mut fields = FormFields::default();
    for pair in body.split(|&b| b == b'&') {
        if pair.is_empty() {
            continue;
        }
        let Some(eq) = pair.iter().position(|&b| b == b'=') else {
            return Err(FormDecodeError);
        };
        let (key, value) = (&pair[..eq], &pair[eq + 1..]);
        let key = decode_bytes(key)?;
        match key.as_slice() {
            b"ssid" => decode_into(&mut fields.ssid, value)?,
            b"password" => decode_into(&mut fields.password, value)?,
            b"server" => decode_into(&mut fields.server, value)?,
            b"device_name" => decode_into(&mut fields.device_name, value)?,
            b"tailscale_key" => decode_into(&mut fields.tailscale_key, value)?,
            _ => {}
        }
    }
    Ok(fields)
}

/// The portal's routing table over one request: the HTTP status and the
/// page to send. A valid submission is saved through the store first; a
/// rejected one saves nothing, so the device is never left half-configured.
pub fn route<S: DeviceConfigStore>(
    store: &mut S,
    method: Method,
    path: &str,
    body: &[u8],
) -> (u16, &'static str) {
    match (method, path) {
        (Method::Get, "/") => (200, FORM_HTML),
        (Method::Post, "/submit") => match decode_form(body) {
            Err(_) => (400, DECODE_FAILED_HTML),
            Ok(fields) => match build_config(&fields.submission()) {
                Err(error) => (400, rejection_html(error)),
                Ok(config) => match store.save(&config) {
                    // A store failure is the device's fault, not the form's.
                    Err(_) => (500, SAVE_FAILED_HTML),
                    Ok(()) => (200, SAVED_HTML),
                },
            },
        },
        _ => (404, NOT_FOUND_HTML),
    }
}

/// The rejection page naming why a submission was refused.
const fn rejection_html(error: ProvisionError) -> &'static str {
    match error {
        ProvisionError::SsidRequired => REJECT_SSID_REQUIRED,
        ProvisionError::SsidTooLong => REJECT_SSID_TOO_LONG,
        ProvisionError::PasswordTooLong => REJECT_PASSWORD_TOO_LONG,
        ProvisionError::ServerAddressRequired => REJECT_SERVER_REQUIRED,
        ProvisionError::ServerHostTooLong => REJECT_SERVER_TOO_LONG,
        ProvisionError::InvalidServerAddress => REJECT_SERVER_INVALID,
        ProvisionError::DeviceNameTooLong => REJECT_NAME_TOO_LONG,
        ProvisionError::AuthKeyTooLong => REJECT_KEY_TOO_LONG,
        ProvisionError::WifiCredentialsRejected(_) => REJECT_WIFI_CREDENTIALS,
    }
}

/// Decodes one value into a string field.
fn decode_into<const N: usize>(
    out: &mut HeaplessString<N>,
    raw: &[u8],
) -> Result<(), FormDecodeError> {
    let bytes = decode_bytes(raw)?;
    let text = core::str::from_utf8(&bytes).map_err(|_| FormDecodeError)?;
    out.clear();
    out.push_str(text).map_err(|_| FormDecodeError)
}

/// Percent-decodes a key or value into bytes, rejecting bad escapes and
/// over-long results.
fn decode_bytes(raw: &[u8]) -> Result<heapless::Vec<u8, FORM_MAX_BYTES>, FormDecodeError> {
    let mut bytes = heapless::Vec::new();
    let mut iter = raw.iter().copied();
    while let Some(b) = iter.next() {
        let decoded = match b {
            b'+' => b' ',
            b'%' => {
                let Some(high) = iter.next().and_then(nibble) else {
                    return Err(FormDecodeError);
                };
                let Some(low) = iter.next().and_then(nibble) else {
                    return Err(FormDecodeError);
                };
                // Both nibbles are at most 15, so the byte fits.
                high * 16 + low
            }
            _ => b,
        };
        bytes.push(decoded).map_err(|_| FormDecodeError)?;
    }
    Ok(bytes)
}

/// One hex digit; `None` when the byte is not `[0-9a-fA-F]`.
fn nibble(b: u8) -> Option<u8> {
    match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        b'A'..=b'F' => Some(b - b'A' + 10),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::adapters::testing::InMemoryConfigStore;
    use crate::config;
    use crate::domain::{DeviceConfig, SSID_MAX_LEN};

    /// A valid urlencoded body in the form's shape.
    fn valid_body() -> String {
        "ssid=home&password=hunter2&server=yourserver%3A8000&device_name=kitchen".to_string()
    }

    #[test]
    fn the_form_decodes_plain_and_escaped_values() {
        let fields = decode_form(valid_body().as_bytes()).unwrap();
        assert_eq!(fields.ssid.as_str(), "home");
        assert_eq!(fields.password.as_str(), "hunter2");
        assert_eq!(fields.server.as_str(), "yourserver:8000");
        assert_eq!(fields.device_name.as_str(), "kitchen");
        assert_eq!(fields.tailscale_key.as_str(), "");
    }

    #[test]
    fn plus_means_space_and_percent_escapes_decode_bytewise() {
        let fields =
            decode_form(b"ssid=my%20network&password=p%2B%40ss%26w%C3%BCrd&server=yourserver")
                .unwrap();
        assert_eq!(fields.ssid.as_str(), "my network");
        assert_eq!(fields.password.as_str(), "p+@ss&wü\u{0072}d");
        assert_eq!(fields.server.as_str(), "yourserver");
    }

    #[test]
    fn unknown_keys_are_ignored_and_blank_pairs_skipped() {
        let fields = decode_form(b"&future=1&ssid=home&&server=yourserver&").unwrap();
        assert_eq!(fields.ssid.as_str(), "home");
        assert_eq!(fields.server.as_str(), "yourserver");
    }

    #[test]
    fn malformed_bodies_are_rejected() {
        for body in [
            &b"ssid"[..],           // pair without '='
            &b"ssid=ok&bad%zz"[..], // malformed escape
            &b"ssid=ok%2"[..],      // truncated escape
            &b"ssid=%C3"[..],       // invalid UTF-8
            &[b'x'; 300][..],       // runaway field
        ] {
            assert!(
                decode_form(body).is_err(),
                "{:?} should not decode",
                String::from_utf8_lossy(body)
            );
        }
    }

    #[test]
    fn the_form_is_served_and_valid_submissions_are_saved() {
        let mut store = InMemoryConfigStore::default();
        let (status, body) = route(&mut store, Method::Get, "/", b"");
        assert_eq!(status, 200);
        assert!(body.contains("/submit"));
        assert!(body.contains("ssid"));

        let (status, body) = route(&mut store, Method::Post, "/submit", valid_body().as_bytes());
        assert_eq!(status, 200, "{body}");
        let config = store.load().unwrap().expect("config saved");
        assert_eq!(config.networks[0].ssid.as_str(), "home");
        assert_eq!(config.server_port, 8000);
        assert_eq!(config.device_name.as_str(), "kitchen");
    }

    #[test]
    fn invalid_submissions_are_rejected_without_touching_the_store() {
        let mut store = InMemoryConfigStore::default();

        // No SSID.
        let (status, body) = route(&mut store, Method::Post, "/submit", b"server=yourserver");
        assert_eq!(status, 400);
        assert!(body.contains("required"));
        assert_eq!(store.load().unwrap(), None);

        // Malformed server address: the config stays empty.
        let (status, _) = route(
            &mut store,
            Method::Post,
            "/submit",
            b"ssid=home&server=yourserver%3Aabc",
        );
        assert_eq!(status, 400);
        assert_eq!(store.load().unwrap(), None);
    }

    #[test]
    fn over_long_values_are_named_by_the_use_case_not_the_decoder() {
        let mut store = InMemoryConfigStore::default();
        let long_ssid = "x".repeat(SSID_MAX_LEN + 1);
        let body = format!("ssid={long_ssid}&server=yourserver");
        let (status, body) = route(&mut store, Method::Post, "/submit", body.as_bytes());
        assert_eq!(status, 400);
        assert!(body.contains("too long"), "{body}");
        assert_eq!(store.load().unwrap(), None);
    }

    #[test]
    fn unknown_paths_are_not_found() {
        let mut store = InMemoryConfigStore::default();
        let (status, body) = route(&mut store, Method::Get, "/nope", b"");
        assert_eq!(status, 404);
        assert!(body.contains("Not found"));
    }

    #[test]
    fn a_saved_submission_carries_every_field() {
        let mut store = InMemoryConfigStore::default();
        let body = "ssid=home&password=pw&server=192.168.1.20%3A9000&device_name=&tailscale_key=tskey-auth-x";
        let (status, _) = route(&mut store, Method::Post, "/submit", body.as_bytes());
        assert_eq!(status, 200);
        let config: DeviceConfig = store.load().unwrap().unwrap();
        assert_eq!(config.server_host.as_str(), "192.168.1.20");
        assert_eq!(config.server_port, 9000);
        // Blank device name keeps the compile-time default.
        assert_eq!(config.device_name.as_str(), config::DEVICE_NAME);
        assert_eq!(config.tailscale_auth_key.as_str(), "tskey-auth-x");
    }
}
