//! [`ComicSource`] and [`ComicTrigger`] over the Comack server's HTTP API.

use core::fmt::Write as _;
use core::net::Ipv4Addr;

use log::warn;

use super::buf::SliceWriter;
use super::http::{HttpClient, HttpError, Method};
use super::identity::{DeviceIdentity, IdentityPath};
use super::json::parse_issue_number;
use super::net::TcpConnector;
use crate::application::ports::{ComicSource, ComicTrigger, PressReporter};
use crate::domain::{Button, ColorFrame, Frame, Issue, COLOR_FRAME_BYTES, FRAME_BYTES};

/// Where the device reports button presses; tokenless like the comic read
/// routes, because the device carries no token (`server/auth.py`).
const BUTTONS_PATH: &str = "/api/device/buttons";

/// Why a comic API call failed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ComicApiError {
    /// Transport or protocol failure.
    Http(HttpError),
    /// The server answered with an unexpected status.
    Status(u16),
    /// The frame was shorter than [`FRAME_BYTES`] (or [`COLOR_FRAME_BYTES`]
    /// for a colour image).
    Incomplete {
        /// Bytes actually received.
        received: usize,
    },
    /// `/api/comics/current` succeeded but carried no usable issue number.
    MissingIssueNumber,
}

impl From<HttpError> for ComicApiError {
    fn from(e: HttpError) -> Self {
        Self::Http(e)
    }
}

/// The Comack server at a resolved address.
#[derive(Clone, Copy, Debug)]
pub struct HttpComicService<'a, C> {
    http: HttpClient<'a, C>,
    server: Ipv4Addr,
    identity: DeviceIdentity<'a>,
}

impl<'a, C: TcpConnector> HttpComicService<'a, C> {
    /// Talk to the server at `server` through `http`. The `identity` rides
    /// on the identity-aware requests — the comic poll and the trigger — as
    /// `?device_id=&device_token=`; [`DeviceIdentity::NONE`] keeps the
    /// tokenless wire behavior, which the server binds to its `default`
    /// device.
    pub const fn new(
        http: HttpClient<'a, C>,
        server: Ipv4Addr,
        identity: DeviceIdentity<'a>,
    ) -> Self {
        Self {
            http,
            server,
            identity,
        }
    }

    /// The comic poll, carrying this device's identity.
    fn current_path(&self) -> IdentityPath<'_> {
        IdentityPath {
            base: "/api/comics/current",
            identity: &self.identity,
        }
    }

    /// Downloads the colour image of `issue` for a Spectra 6 panel. Same
    /// identity-free route family as [`ComicSource::fetch`].
    ///
    /// Pulled in four 48 KB parts (`/color.bin/0`…`/3`): MicroLink/WG often
    /// truncates a single 192 KB GET. Each part is retried — the server usually
    /// finishes the response; the device TCP path drops mid-body.
    ///
    /// # Errors
    /// Transport failures, a non-200 status, or a short body after retries.
    pub fn fetch_color(
        &mut self,
        issue: Issue,
        image: &mut ColorFrame,
    ) -> Result<(), ComicApiError> {
        const PARTS: usize = 4;
        const PART_BYTES: usize = COLOR_FRAME_BYTES / PARTS;
        const ATTEMPTS: usize = 5;
        let mut received = 0;
        for part in 0..PARTS {
            let dest = &mut image[part * PART_BYTES..(part + 1) * PART_BYTES];
            let path = format_args!("/api/comics/{}/color.bin/{part}", issue.get());
            let mut last = ComicApiError::Incomplete { received: 0 };
            let mut ok = false;
            for attempt in 0..ATTEMPTS {
                match self.http.request(self.server, Method::Get, path, dest) {
                    Ok(resp) if resp.status == 200 && resp.body_len == PART_BYTES => {
                        received += PART_BYTES;
                        ok = true;
                        break;
                    }
                    Ok(resp) if resp.status != 200 => {
                        last = ComicApiError::Status(resp.status);
                        if resp.status == 404 {
                            return Err(last);
                        }
                    }
                    Ok(resp) => {
                        last = ComicApiError::Incomplete {
                            received: received + resp.body_len,
                        };
                    }
                    Err(e) => last = ComicApiError::Http(e),
                }
                if attempt + 1 < ATTEMPTS {
                    warn!(
                        "Colour part {part}/{} for comic {} incomplete ({last:?}); retrying",
                        PARTS - 1,
                        issue.get()
                    );
                }
            }
            if !ok {
                return Err(last);
            }
        }
        Ok(())
    }

    /// The trigger, carrying this device's identity so the refresh is
    /// attributed to this device's pipeline.
    fn trigger_path(&self) -> IdentityPath<'_> {
        IdentityPath {
            base: "/api/trigger",
            identity: &self.identity,
        }
    }
}

impl<C: TcpConnector> ComicSource for HttpComicService<'_, C> {
    type Error = ComicApiError;

    fn latest_issue(&mut self) -> Result<Option<Issue>, ComicApiError> {
        let mut body = [0u8; 256];
        let resp = self
            .http
            .request(self.server, Method::Get, self.current_path(), &mut body)?;
        match resp.status {
            // Nothing has been revealed yet.
            404 => Ok(None),
            200 => parse_issue_number(&body[..resp.body_len])
                .and_then(Issue::new)
                .map(Some)
                .ok_or(ComicApiError::MissingIssueNumber),
            status => Err(ComicApiError::Status(status)),
        }
    }

    fn fetch(&mut self, issue: Issue, frame: &mut Frame) -> Result<(), ComicApiError> {
        // Deliberately identity-free (spec D2): issue numbers are a global
        // namespace and the tailnet is the trust boundary.
        let path = format_args!("/api/comics/{}/bin", issue.get());
        let resp = self.http.request(self.server, Method::Get, path, frame)?;
        if resp.status != 200 {
            return Err(ComicApiError::Status(resp.status));
        }
        if resp.body_len != FRAME_BYTES {
            return Err(ComicApiError::Incomplete {
                received: resp.body_len,
            });
        }
        Ok(())
    }
}

impl<C: TcpConnector> ComicTrigger for HttpComicService<'_, C> {
    type Error = ComicApiError;

    fn trigger(&mut self) -> Result<(), ComicApiError> {
        let mut ignored = [0u8; 64];
        let resp =
            self.http
                .request(self.server, Method::Post, self.trigger_path(), &mut ignored)?;
        if resp.is_success() {
            Ok(())
        } else {
            Err(ComicApiError::Status(resp.status))
        }
    }
}

impl<C: TcpConnector> PressReporter for HttpComicService<'_, C> {
    fn report(&mut self, button: Button) {
        let mut body = [0u8; 24];
        let mut writer = SliceWriter::new(&mut body);
        // Fixed sizes: the writer can only overflow on a bug, and even then
        // the report is fire-and-forget.
        if write!(writer, r#"{{"button":"{}"}}"#, button.as_str()).is_err() {
            warn!("Button report: body did not fit");
            return;
        }
        let mut ignored = [0u8; 0];
        match self.http.request_with_body(
            self.server,
            Method::Post,
            BUTTONS_PATH,
            writer.written(),
            &mut ignored,
        ) {
            Ok(resp) if resp.is_success() => {}
            Ok(resp) => warn!("Button report rejected: status {}", resp.status),
            Err(e) => warn!("Button report failed: {e:?}"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::super::http::HttpConfig;
    use super::super::net::NetError;
    use super::super::testing::ScriptedConnector;
    use super::*;
    use std::vec::Vec;

    const SERVER: Ipv4Addr = Ipv4Addr::new(100, 64, 0, 9);

    fn service(connector: ScriptedConnector) -> HttpComicService<'static, ScriptedConnector> {
        service_with(connector, "", "")
    }

    fn service_with(
        connector: ScriptedConnector,
        device_id: &'static str,
        device_token: &'static str,
    ) -> HttpComicService<'static, ScriptedConnector> {
        HttpComicService::new(
            HttpClient::new(connector, "localhost", 8000, HttpConfig::default()),
            SERVER,
            DeviceIdentity {
                device_id,
                device_token,
            },
        )
    }

    fn reply(status: &str, body: &[u8]) -> ScriptedConnector {
        let mut r = std::format!("HTTP/1.1 {status}\r\ncontent-type: x\r\n\r\n").into_bytes();
        r.extend_from_slice(body);
        ScriptedConnector::replying(&[&r])
    }

    fn request_line(c: &ScriptedConnector) -> Vec<u8> {
        let sent = c.sent();
        sent[..sent.iter().position(|b| *b == b'\r').unwrap()].to_vec()
    }

    #[test]
    fn latest_issue_reads_current_comic() {
        let c = reply("200 OK", br#"{"issue_number":12,"title":"t"}"#);
        assert_eq!(service(c.clone()).latest_issue(), Ok(Issue::new(12)));
        assert_eq!(request_line(&c), b"GET /api/comics/current HTTP/1.1");
    }

    #[test]
    fn latest_issue_is_none_before_first_reveal() {
        let c = reply("404 Not Found", br#"{"detail":"No displayed comic yet"}"#);
        assert_eq!(service(c).latest_issue(), Ok(None));
    }

    #[test]
    fn latest_issue_rejects_bad_answers() {
        assert_eq!(
            service(reply("500 Oops", b"")).latest_issue(),
            Err(ComicApiError::Status(500))
        );
        assert_eq!(
            service(reply("200 OK", br#"{"issue_number":0}"#)).latest_issue(),
            Err(ComicApiError::MissingIssueNumber)
        );
        let down = ScriptedConnector::default();
        down.0.borrow_mut().refuse_connect = true;
        assert_eq!(
            service(down).latest_issue(),
            Err(ComicApiError::Http(HttpError::Connect(
                NetError::ConnectFailed
            )))
        );
    }

    #[test]
    fn fetch_downloads_a_full_frame() {
        let pixels: Vec<u8> = (0..FRAME_BYTES).map(|i| (i % 251) as u8).collect();
        let c = reply("200 OK", &pixels);
        let mut frame = [0u8; FRAME_BYTES];
        service(c.clone())
            .fetch(Issue::new(7).unwrap(), &mut frame)
            .unwrap();
        assert_eq!(frame.as_slice(), pixels.as_slice());
        assert_eq!(request_line(&c), b"GET /api/comics/7/bin HTTP/1.1");
    }

    #[test]
    fn fetch_color_downloads_a_full_colour_image() {
        let pixels: Vec<u8> = (0..COLOR_FRAME_BYTES).map(|i| (i % 249) as u8).collect();
        let parts: Vec<Vec<u8>> = (0..4)
            .map(|part| {
                let mut r =
                    std::format!("HTTP/1.1 200 OK\r\ncontent-type: x\r\n\r\n").into_bytes();
                let start = part * (COLOR_FRAME_BYTES / 4);
                r.extend_from_slice(&pixels[start..start + COLOR_FRAME_BYTES / 4]);
                r
            })
            .collect();
        let c = ScriptedConnector::replying(&parts.iter().map(|p| p.as_slice()).collect::<Vec<_>>());
        let mut image: Box<ColorFrame> = std::vec![0u8; COLOR_FRAME_BYTES]
            .into_boxed_slice()
            .try_into()
            .unwrap();
        service(c.clone())
            .fetch_color(Issue::new(7).unwrap(), &mut image)
            .unwrap();
        assert_eq!(image.as_slice(), pixels.as_slice());
        let sent = c.sent();
        // Four GETs: /color.bin/0 … /color.bin/3
        assert!(sent.windows(b"/color.bin/0".len()).any(|w| w == b"/color.bin/0"));
        assert!(sent.windows(b"/color.bin/3".len()).any(|w| w == b"/color.bin/3"));
    }

    #[test]
    fn fetch_color_rejects_short_images_and_missing_issues() {
        let mut image: Box<ColorFrame> = std::vec![0u8; COLOR_FRAME_BYTES]
            .into_boxed_slice()
            .try_into()
            .unwrap();
        let issue = Issue::new(3).unwrap();
        // One short reply: the client keeps reading until the script is empty
        // (stands in for Connection: close), then retries exhaust the wire.
        assert!(matches!(
            service(reply("200 OK", &[0xAA; 100])).fetch_color(issue, &mut image),
            Err(ComicApiError::Incomplete { .. } | ComicApiError::Http(_))
        ));
        assert_eq!(
            service(reply("404 Not Found", b"")).fetch_color(issue, &mut image),
            Err(ComicApiError::Status(404))
        );
    }

    #[test]
    fn fetch_rejects_short_frames_and_missing_issues() {
        let mut frame = [0u8; FRAME_BYTES];
        let issue = Issue::new(3).unwrap();
        assert_eq!(
            service(reply("200 OK", &[0xAA; 100])).fetch(issue, &mut frame),
            Err(ComicApiError::Incomplete { received: 100 })
        );
        assert_eq!(
            service(reply("404 Not Found", br#"{"detail":"x"}"#)).fetch(issue, &mut frame),
            Err(ComicApiError::Status(404))
        );
    }

    #[test]
    fn latest_issue_presents_the_device_identity() {
        let c = reply("200 OK", br#"{"issue_number":12,"title":"t"}"#);
        assert_eq!(
            service_with(c.clone(), "living-room", "t0ken").latest_issue(),
            Ok(Issue::new(12))
        );
        assert_eq!(
            request_line(&c),
            b"GET /api/comics/current?device_id=living-room&device_token=t0ken HTTP/1.1".as_slice()
        );
    }

    #[test]
    fn trigger_presents_the_device_identity() {
        let c = reply("200 OK", br#"{"status":"ok"}"#);
        service_with(c.clone(), "living-room", "t0ken")
            .trigger()
            .unwrap();
        assert_eq!(
            request_line(&c),
            b"POST /api/trigger?device_id=living-room&device_token=t0ken HTTP/1.1".as_slice()
        );
    }

    #[test]
    fn fetch_stays_identity_free() {
        // Even for a device that has identity: the bin fetch is the global
        // namespace (spec D2), so the request line carries no pair.
        let pixels = [0u8; FRAME_BYTES];
        let c = reply("200 OK", &pixels);
        let mut frame = [0u8; FRAME_BYTES];
        service_with(c.clone(), "living-room", "t0ken")
            .fetch(Issue::new(7).unwrap(), &mut frame)
            .unwrap();
        assert_eq!(request_line(&c), b"GET /api/comics/7/bin HTTP/1.1");
    }

    #[test]
    fn a_tokenless_service_keeps_the_bare_paths() {
        // DeviceIdentity::NONE is today's wire behavior: no query at all, so
        // the server binds these calls to its `default` device.
        let c = reply("200 OK", br#"{"issue_number":12,"title":"t"}"#);
        assert_eq!(service(c.clone()).latest_issue(), Ok(Issue::new(12)));
        assert_eq!(request_line(&c), b"GET /api/comics/current HTTP/1.1");
        let c = reply("200 OK", br#"{"status":"ok"}"#);
        service(c.clone()).trigger().unwrap();
        assert_eq!(request_line(&c), b"POST /api/trigger HTTP/1.1");
    }

    #[test]
    fn a_partial_identity_is_sent_as_presented() {
        // An id without its token goes out as-is and the server rejects it;
        // the firmware never silently strips a broken identity down to
        // tokenless.
        let c = reply("200 OK", b"");
        service_with(c.clone(), "living-room", "")
            .trigger()
            .unwrap();
        assert_eq!(
            request_line(&c),
            b"POST /api/trigger?device_id=living-room&device_token= HTTP/1.1".as_slice()
        );
    }

    #[test]
    fn trigger_posts() {
        let c = reply("200 OK", br#"{"status":"ok"}"#);
        assert_eq!(service(c.clone()).trigger(), Ok(()));
        assert_eq!(request_line(&c), b"POST /api/trigger HTTP/1.1");
        assert_eq!(
            service(reply("503 Busy", b"")).trigger(),
            Err(ComicApiError::Status(503))
        );
    }

    fn sent_body(c: &ScriptedConnector) -> Vec<u8> {
        let sent = c.sent();
        let header_end = sent
            .windows(4)
            .position(|w| w == b"\r\n\r\n")
            .expect("request has headers")
            + 4;
        sent[header_end..].to_vec()
    }

    #[test]
    fn report_posts_the_button_name() {
        let cases = [
            (Button::Forward, br#"{"button":"forward"}"#.as_slice()),
            (Button::Back, br#"{"button":"back"}"#.as_slice()),
            (Button::Trigger, br#"{"button":"trigger"}"#.as_slice()),
        ];
        for (button, expected) in cases {
            let c = reply("200 OK", b"");
            service(c.clone()).report(button);
            assert_eq!(
                request_line(&c),
                b"POST /api/device/buttons HTTP/1.1".as_slice()
            );
            assert_eq!(sent_body(&c), expected);
        }
    }

    #[test]
    fn a_failed_report_is_a_noop() {
        // Transport error: must not panic or retry.
        let down = ScriptedConnector::default();
        down.0.borrow_mut().refuse_connect = true;
        service(down).report(Button::Back);
        // Non-2xx: logged and dropped.
        service(reply("500 Boom", b"")).report(Button::Back);
    }
}
