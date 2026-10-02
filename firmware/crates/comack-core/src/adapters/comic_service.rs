//! [`ComicSource`] and [`ComicTrigger`] over the Comack server's HTTP API.

use core::net::Ipv4Addr;

use super::http::{HttpClient, HttpError, Method};
use super::json::parse_issue_number;
use super::net::TcpConnector;
use crate::application::ports::{ComicSource, ComicTrigger};
use crate::domain::{Frame, Issue, FRAME_BYTES};

/// Why a comic API call failed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ComicApiError {
    /// Transport or protocol failure.
    Http(HttpError),
    /// The server answered with an unexpected status.
    Status(u16),
    /// The frame was shorter than [`FRAME_BYTES`].
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
}

impl<'a, C: TcpConnector> HttpComicService<'a, C> {
    /// Talk to the server at `server` through `http`.
    pub const fn new(http: HttpClient<'a, C>, server: Ipv4Addr) -> Self {
        Self { http, server }
    }
}

impl<C: TcpConnector> ComicSource for HttpComicService<'_, C> {
    type Error = ComicApiError;

    fn latest_issue(&mut self) -> Result<Option<Issue>, ComicApiError> {
        let mut body = [0u8; 256];
        let resp = self
            .http
            .request(self.server, Method::Get, "/api/comics/current", &mut body)?;
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
        let resp = self
            .http
            .request(self.server, Method::Post, "/api/trigger", &mut ignored)?;
        if resp.is_success() {
            Ok(())
        } else {
            Err(ComicApiError::Status(resp.status))
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
        HttpComicService::new(
            HttpClient::new(connector, "comack-server", 8000, HttpConfig::default()),
            SERVER,
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
    fn trigger_posts() {
        let c = reply("200 OK", br#"{"status":"ok"}"#);
        assert_eq!(service(c.clone()).trigger(), Ok(()));
        assert_eq!(request_line(&c), b"POST /api/trigger HTTP/1.1");
        assert_eq!(
            service(reply("503 Busy", b"")).trigger(),
            Err(ComicApiError::Status(503))
        );
    }
}
