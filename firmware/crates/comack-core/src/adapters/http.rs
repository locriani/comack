//! A minimal HTTP/1.1 client for bodiless requests, over any [`TcpStream`].
//!
//! The device only needs `GET` (and a bodiless `POST`) with
//! `Connection: close`, reading the body into a caller-supplied buffer.

use core::fmt::{self, Write as _};
use core::net::{Ipv4Addr, SocketAddrV4};
use core::time::Duration;

use super::buf::SliceWriter;
use super::net::{NetError, TcpConnector, TcpStream};

/// Request method.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Method {
    /// `GET`
    Get,
    /// `POST` with an empty body.
    Post,
}

impl Method {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Get => "GET",
            Self::Post => "POST",
        }
    }
}

/// HTTP client failures.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HttpError {
    /// Could not connect.
    Connect(NetError),
    /// Could not send the request.
    Send(NetError),
    /// The request does not fit in the request buffer.
    RequestTooLarge,
    /// The server closed or went quiet before sending a status line.
    MalformedResponse,
}

/// Status and body size of a completed request.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct HttpResponse {
    /// HTTP status code.
    pub status: u16,
    /// Body bytes copied into the caller's buffer.
    pub body_len: usize,
}

impl HttpResponse {
    /// Whether the status is 2xx.
    pub const fn is_success(&self) -> bool {
        self.status >= 200 && self.status < 300
    }
}

/// Client timeouts.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct HttpConfig {
    /// Give up connecting after this long.
    pub connect_timeout: Duration,
    /// Give up when the server is silent for this long.
    pub recv_timeout: Duration,
}

impl Default for HttpConfig {
    fn default() -> Self {
        Self {
            connect_timeout: Duration::from_secs(10),
            recv_timeout: Duration::from_secs(5),
        }
    }
}

/// Size of the buffer requests are formatted into.
pub const MAX_REQUEST_LEN: usize = 192;

/// Format a bodiless request with `Connection: close` into `out`.
///
/// # Errors
/// [`HttpError::RequestTooLarge`] if it does not fit.
pub fn write_request(
    out: &mut [u8],
    method: Method,
    path: impl fmt::Display,
    host: &str,
    port: u16,
) -> Result<usize, HttpError> {
    let mut w = SliceWriter::new(out);
    write!(
        w,
        "{} {path} HTTP/1.1\r\nHost: {host}:{port}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        method.as_str()
    )
    .map_err(|_| HttpError::RequestTooLarge)?;
    Ok(w.len())
}

/// Incremental response parser: extracts the status code and finds where the
/// body starts, however the response is split across reads.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ResponseParser {
    phase: Phase,
    status: u16,
    /// Bytes of the `\r\n\r\n` terminator matched so far.
    terminator: u8,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
enum Phase {
    #[default]
    Version,
    Status {
        digits: u8,
    },
    Headers,
    Body,
}

impl ResponseParser {
    /// Consume `chunk`; returns the offset of the first body byte in it
    /// (`chunk.len()` if the body has not started yet).
    pub fn feed(&mut self, chunk: &[u8]) -> usize {
        const TERMINATOR: &[u8; 4] = b"\r\n\r\n";
        for (i, &byte) in chunk.iter().enumerate() {
            match self.phase {
                Phase::Body => return i,
                Phase::Version => {
                    if byte == b' ' {
                        self.phase = Phase::Status { digits: 0 };
                    }
                }
                Phase::Status { digits } => {
                    if byte.is_ascii_digit() && digits < 3 {
                        self.status = self.status * 10 + u16::from(byte - b'0');
                        self.phase = Phase::Status { digits: digits + 1 };
                    } else {
                        if digits != 3 {
                            self.status = 0;
                        }
                        self.phase = Phase::Headers;
                    }
                }
                Phase::Headers => {}
            }
            // Longest prefix of the terminator ending here. The pattern only
            // overlaps itself at "\r", so a mismatch falls back to 0 or 1.
            self.terminator = if byte == TERMINATOR[usize::from(self.terminator)] {
                self.terminator + 1
            } else {
                u8::from(byte == b'\r')
            };
            if self.terminator == 4 {
                self.phase = Phase::Body;
            }
        }
        chunk.len()
    }

    /// The status code, once a complete three-digit one has been read.
    pub fn status(&self) -> Option<u16> {
        match self.phase {
            Phase::Headers | Phase::Body if self.status != 0 => Some(self.status),
            _ => None,
        }
    }

    /// Whether the headers are complete.
    pub fn in_body(&self) -> bool {
        self.phase == Phase::Body
    }
}

/// HTTP client bound to one server.
#[derive(Clone, Copy, Debug)]
pub struct HttpClient<'a, C> {
    connector: C,
    host: &'a str,
    port: u16,
    config: HttpConfig,
}

impl<'a, C: TcpConnector> HttpClient<'a, C> {
    /// `host` goes into the `Host` header; connections go to the address
    /// given per request.
    pub const fn new(connector: C, host: &'a str, port: u16, config: HttpConfig) -> Self {
        Self {
            connector,
            host,
            port,
            config,
        }
    }

    /// Perform a request against `ip`, copying up to `body.len()` body bytes
    /// into `body`. Reading stops when the buffer is full, the server closes
    /// the connection or it is silent for `recv_timeout`.
    ///
    /// # Errors
    /// See [`HttpError`].
    pub fn request(
        &self,
        ip: Ipv4Addr,
        method: Method,
        path: impl fmt::Display,
        body: &mut [u8],
    ) -> Result<HttpResponse, HttpError> {
        let mut request = [0u8; MAX_REQUEST_LEN];
        let request_len = write_request(&mut request, method, path, self.host, self.port)?;

        let mut stream = self
            .connector
            .connect(
                SocketAddrV4::new(ip, self.port),
                self.config.connect_timeout,
            )
            .map_err(HttpError::Connect)?;
        stream
            .send_all(&request[..request_len])
            .map_err(HttpError::Send)?;

        let mut parser = ResponseParser::default();
        let mut body_len = 0;
        let mut chunk = [0u8; 1024];
        while body_len < body.len() || !parser.in_body() {
            let n = match stream.recv(&mut chunk, self.config.recv_timeout) {
                Ok(0) | Err(_) => break,
                Ok(n) => n,
            };
            let start = parser.feed(&chunk[..n]);
            let take = (n - start).min(body.len() - body_len);
            body[body_len..body_len + take].copy_from_slice(&chunk[start..start + take]);
            body_len += take;
        }

        let status = parser.status().ok_or(HttpError::MalformedResponse)?;
        Ok(HttpResponse { status, body_len })
    }
}

#[cfg(test)]
mod tests {
    use super::super::testing::{Recv, ScriptedConnector};
    use super::*;
    use proptest::prelude::*;
    use std::vec::Vec;

    const SERVER: Ipv4Addr = Ipv4Addr::new(100, 64, 0, 1);

    fn client(connector: ScriptedConnector) -> HttpClient<'static, ScriptedConnector> {
        HttpClient::new(connector, "comack-server", 8000, HttpConfig::default())
    }

    #[test]
    fn formats_a_bodiless_request() {
        let mut buf = [0u8; MAX_REQUEST_LEN];
        let n = write_request(&mut buf, Method::Post, "/api/trigger", "comack-server", 8000).unwrap();
        assert_eq!(
            &buf[..n],
            b"POST /api/trigger HTTP/1.1\r\nHost: comack-server:8000\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        );
    }

    #[test]
    fn rejects_requests_that_do_not_fit() {
        let mut buf = [0u8; 16];
        assert_eq!(
            write_request(&mut buf, Method::Get, "/a/very/long/path", "h", 1),
            Err(HttpError::RequestTooLarge)
        );
    }

    #[test]
    fn parses_status_and_body_offset() {
        let mut p = ResponseParser::default();
        let resp = b"HTTP/1.1 404 Not Found\r\nA: b\r\n\r\nbody";
        assert_eq!(p.feed(resp), resp.len() - 4);
        assert_eq!(p.status(), Some(404));
        assert!(p.in_body());
        assert_eq!(p.feed(b"more"), 0, "everything after the headers is body");
    }

    #[test]
    fn status_requires_three_digits() {
        let mut p = ResponseParser::default();
        p.feed(b"HTTP/1.1 20 OK\r\n\r\n");
        assert_eq!(p.status(), None);
        let mut p = ResponseParser::default();
        p.feed(b"garbage");
        assert_eq!(p.status(), None);
    }

    #[test]
    fn terminator_survives_near_misses() {
        let mut p = ResponseParser::default();
        let resp = b"HTTP/1.0 200 OK\r\nX: \r\r\n\r\nB";
        assert_eq!(p.feed(resp), resp.len() - 1);
        let mut p = ResponseParser::default();
        let resp = b"HTTP/1.0 200 OK\r\nX: \r\n\n\r\nY: z\r\n\r\nB";
        assert_eq!(p.feed(resp), resp.len() - 1);
    }

    #[test]
    fn request_reads_body_across_chunks() {
        let connector = ScriptedConnector::replying(&[
            b"HTTP/1.1 200 OK\r\nContent-Le",
            b"ngth: 5\r\n\r",
            b"\nhel",
            b"lo",
        ]);
        let mut body = [0u8; 16];
        let resp = client(connector.clone())
            .request(SERVER, Method::Get, "/x", &mut body)
            .unwrap();
        assert_eq!(
            resp,
            HttpResponse {
                status: 200,
                body_len: 5
            }
        );
        assert!(resp.is_success());
        assert_eq!(&body[..5], b"hello");
        let wire = connector.0.borrow();
        assert_eq!(
            wire.connects,
            [(SocketAddrV4::new(SERVER, 8000), Duration::from_secs(10))]
        );
        assert!(wire.sent.starts_with(b"GET /x HTTP/1.1\r\n"));
        assert!(wire
            .recv_timeouts
            .iter()
            .all(|t| *t == Duration::from_secs(5)));
        assert_eq!(wire.closed, 1, "connection closed after the request");
    }

    #[test]
    fn request_stops_when_buffer_is_full() {
        let connector = ScriptedConnector::replying(&[b"HTTP/1.1 200 OK\r\n\r\n0123456789"]);
        connector.push(Recv::Data(b"never read".to_vec()));
        let mut body = [0u8; 4];
        let resp = client(connector.clone())
            .request(SERVER, Method::Get, "/", &mut body)
            .unwrap();
        assert_eq!(resp.body_len, 4);
        assert_eq!(&body, b"0123");
        assert_eq!(connector.0.borrow().script.len(), 1);
    }

    #[test]
    fn request_with_empty_body_buffer_still_reads_status() {
        let connector = ScriptedConnector::replying(&[b"HTTP/1.1 ", b"204 No Content\r\n\r\n"]);
        let resp = client(connector)
            .request(SERVER, Method::Post, "/", &mut [])
            .unwrap();
        assert_eq!(
            resp,
            HttpResponse {
                status: 204,
                body_len: 0
            }
        );
    }

    #[test]
    fn timeout_ends_the_body() {
        let connector = ScriptedConnector::replying(&[b"HTTP/1.1 200 OK\r\n\r\nab"]);
        connector.push(Recv::Timeout);
        connector.push(Recv::Data(b"late".to_vec()));
        let mut body = [0u8; 8];
        let resp = client(connector)
            .request(SERVER, Method::Get, "/", &mut body)
            .unwrap();
        assert_eq!(resp.body_len, 2);
    }

    #[test]
    fn errors_are_reported() {
        let refused = ScriptedConnector::default();
        refused.0.borrow_mut().refuse_connect = true;
        assert_eq!(
            client(refused).request(SERVER, Method::Get, "/", &mut []),
            Err(HttpError::Connect(NetError::ConnectFailed))
        );

        let broken = ScriptedConnector::default();
        broken.0.borrow_mut().fail_send_at = Some(0);
        assert_eq!(
            client(broken).request(SERVER, Method::Get, "/", &mut []),
            Err(HttpError::Send(NetError::Io))
        );

        let silent = ScriptedConnector::default();
        assert_eq!(
            client(silent).request(SERVER, Method::Get, "/", &mut []),
            Err(HttpError::MalformedResponse)
        );
    }

    proptest! {
        /// However the response is split into reads, the body comes out whole.
        #[test]
        fn body_is_independent_of_chunking(
            body in proptest::collection::vec(any::<u8>(), 0..300),
            cuts in proptest::collection::vec(1usize..40, 0..20),
        ) {
            let mut response = b"HTTP/1.1 200 OK\r\nX-Pad: \r\r\n\r\n".to_vec();
            response.extend_from_slice(&body);
            let connector = ScriptedConnector::default();
            let mut rest = response.as_slice();
            for cut in cuts {
                let n = cut.min(rest.len());
                connector.push(Recv::Data(rest[..n].to_vec()));
                rest = &rest[n..];
            }
            connector.push(Recv::Data(rest.to_vec()));
            let mut out = [0u8; 512];
            let resp = client(connector).request(SERVER, Method::Get, "/", &mut out).unwrap();
            prop_assert_eq!(resp.status, 200);
            prop_assert_eq!(&out[..resp.body_len], body.as_slice());
        }

        #[test]
        fn parser_never_panics(input in proptest::collection::vec(any::<u8>(), 0..256)) {
            let mut p = ResponseParser::default();
            let start = p.feed(&input);
            prop_assert!(start <= input.len());
            let _: Vec<u8> = input[start..].to_vec();
        }
    }
}
