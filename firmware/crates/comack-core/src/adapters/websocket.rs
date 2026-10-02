//! A minimal RFC 6455 client: upgrade handshake, masked binary frames out,
//! ping answered with pong. Enough for the server's `/ws/audio`.

use core::fmt::Write as _;
use core::net::SocketAddrV4;
use core::time::Duration;

use super::buf::SliceWriter;
use super::net::{HostResolver, NetError, ServerLocator, TcpConnector, TcpStream};
use crate::application::ports::{AudioSession, AudioUplink};

/// The handshake key. The server's accept hash is not verified, so a fixed
/// key (the RFC's sample nonce) is fine.
pub const HANDSHAKE_KEY: &str = "dGhlIHNhbXBsZSBub25jZQ==";

/// Longest client frame header: 2 + 8 (64-bit length) + 4 (mask key).
pub const MAX_HEADER_LEN: usize = 14;

/// Frame opcodes used here.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum Opcode {
    /// Binary data frame.
    Binary = 0x2,
    /// Connection close.
    Close = 0x8,
    /// Ping.
    Ping = 0x9,
    /// Pong.
    Pong = 0xA,
}

/// WebSocket failures. Any of them means the connection is dead.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum WsError {
    /// The server address is not known yet.
    Unresolved,
    /// Could not connect.
    Connect(NetError),
    /// Transport failure after connecting.
    Io(NetError),
    /// The handshake request does not fit in its buffer.
    RequestTooLarge,
    /// The server did not answer the upgrade with `101`.
    HandshakeRejected,
    /// The server sent a close frame.
    ClosedByServer,
}

/// Encode the header of a final, masked client frame carrying `payload_len`
/// bytes. The mask key is all zeros, which the RFC allows and which leaves
/// the payload bytes unchanged. Returns the header length.
#[allow(clippy::cast_possible_truncation)] // each arm's range fits its field
pub fn encode_client_header(
    opcode: Opcode,
    payload_len: usize,
    out: &mut [u8; MAX_HEADER_LEN],
) -> usize {
    const MASKED: u8 = 0x80;
    out[0] = 0x80 | opcode as u8;
    let mut len = match payload_len {
        0..=125 => {
            // Fits in the 7-bit length field.
            out[1] = MASKED | payload_len as u8;
            2
        }
        126..=0xFFFF => {
            out[1] = MASKED | 0x7E; // 126: 16-bit length follows
            out[2..4].copy_from_slice(&(payload_len as u16).to_be_bytes());
            4
        }
        _ => {
            out[1] = MASKED | 0x7F; // 127: 64-bit length follows
            out[2..10].copy_from_slice(&(payload_len as u64).to_be_bytes());
            10
        }
    };
    out[len..len + 4].fill(0);
    len += 4;
    len
}

/// Format the upgrade request into `out`.
///
/// # Errors
/// [`WsError::RequestTooLarge`] if it does not fit.
pub fn write_handshake(
    out: &mut [u8],
    host: &str,
    port: u16,
    path: &str,
) -> Result<usize, WsError> {
    let mut w = SliceWriter::new(out);
    write!(
        w,
        "GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\
         Sec-WebSocket-Key: {HANDSHAKE_KEY}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    .map_err(|_| WsError::RequestTooLarge)?;
    Ok(w.len())
}

/// Whether the response's status line is `101 Switching Protocols`.
pub fn is_handshake_accepted(response: &[u8]) -> bool {
    let status_line = response.split(|b| *b == b'\r').next().unwrap_or(&[]);
    status_line.windows(5).any(|w| w == b" 101 ") || status_line.ends_with(b" 101")
}

/// What a server frame at the start of a read means to this client.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ServerFrame<'a> {
    /// A ping with its application data, to be echoed in a pong.
    Ping(&'a [u8]),
    /// The server is closing the connection.
    Close,
    /// Anything else (or a fragment), ignored.
    Other,
}

/// Interpret the first frame in `buf`. Control frames are small and arrive
/// whole in practice; a partial one is ignored.
pub fn parse_server_frame(buf: &[u8]) -> ServerFrame<'_> {
    let [first, second, rest @ ..] = buf else {
        return ServerFrame::Other;
    };
    let opcode = first & 0x0F;
    let len = usize::from(second & 0x7F);
    let masked = second & 0x80 != 0;
    match opcode {
        0x8 => ServerFrame::Close,
        // Server frames are never masked, and control payloads fit in 125.
        0x9 if !masked && len <= 125 && len <= rest.len() => ServerFrame::Ping(&rest[..len]),
        _ => ServerFrame::Other,
    }
}

/// Timeouts for opening a WebSocket.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct WsConfig {
    /// Give up connecting after this long.
    pub connect_timeout: Duration,
    /// Give up waiting for the upgrade response after this long.
    pub handshake_timeout: Duration,
}

impl Default for WsConfig {
    fn default() -> Self {
        Self {
            connect_timeout: Duration::from_secs(5),
            handshake_timeout: Duration::from_secs(5),
        }
    }
}

/// An open client WebSocket.
pub struct WebSocket<S> {
    stream: S,
}

impl<S: TcpStream> WebSocket<S> {
    /// Connect to `addr` and perform the upgrade for `path`.
    ///
    /// # Errors
    /// See [`WsError`].
    pub fn connect<C>(
        connector: &C,
        addr: SocketAddrV4,
        host: &str,
        path: &str,
        config: WsConfig,
    ) -> Result<Self, WsError>
    where
        C: TcpConnector<Stream = S>,
    {
        let mut request = [0u8; 256];
        let n = write_handshake(&mut request, host, addr.port(), path)?;
        let mut stream = connector
            .connect(addr, config.connect_timeout)
            .map_err(WsError::Connect)?;
        stream.send_all(&request[..n]).map_err(WsError::Io)?;

        let mut response = [0u8; 256];
        let n = stream
            .recv(&mut response, config.handshake_timeout)
            .map_err(WsError::Io)?;
        if !is_handshake_accepted(&response[..n]) {
            return Err(WsError::HandshakeRejected);
        }
        Ok(Self { stream })
    }

    /// Send `payload` as one binary frame.
    ///
    /// # Errors
    /// [`WsError::Io`] if the stream fails.
    pub fn send_binary(&mut self, payload: &[u8]) -> Result<(), WsError> {
        self.send_frame(Opcode::Binary, payload)?;
        self.service_control_frames()
    }

    /// Send 16-bit samples, little-endian, as one binary frame, without
    /// staging the whole chunk in memory.
    ///
    /// # Errors
    /// [`WsError::Io`] if the stream fails.
    pub fn send_pcm(&mut self, pcm: &[i16]) -> Result<(), WsError> {
        let mut header = [0u8; MAX_HEADER_LEN];
        let n = encode_client_header(Opcode::Binary, pcm.len() * 2, &mut header);
        self.stream.send_all(&header[..n]).map_err(WsError::Io)?;
        let mut bytes = [0u8; 512];
        for block in pcm.chunks(bytes.len() / 2) {
            for (dst, sample) in bytes.chunks_exact_mut(2).zip(block) {
                dst.copy_from_slice(&sample.to_le_bytes());
            }
            self.stream
                .send_all(&bytes[..block.len() * 2])
                .map_err(WsError::Io)?;
        }
        self.service_control_frames()
    }

    fn send_frame(&mut self, opcode: Opcode, payload: &[u8]) -> Result<(), WsError> {
        let mut header = [0u8; MAX_HEADER_LEN];
        let n = encode_client_header(opcode, payload.len(), &mut header);
        self.stream.send_all(&header[..n]).map_err(WsError::Io)?;
        if !payload.is_empty() {
            self.stream.send_all(payload).map_err(WsError::Io)?;
        }
        Ok(())
    }

    /// Answer a pending ping, notice a close. The server (uvicorn) pings
    /// every 20 s and drops clients that do not answer.
    fn service_control_frames(&mut self) -> Result<(), WsError> {
        let mut incoming = [0u8; 128];
        let n = self
            .stream
            .recv(&mut incoming, Duration::ZERO)
            .map_err(WsError::Io)?;
        match parse_server_frame(&incoming[..n]) {
            ServerFrame::Close => Err(WsError::ClosedByServer),
            ServerFrame::Ping(data) => {
                let mut pong = [0u8; 125];
                pong[..data.len()].copy_from_slice(data);
                let len = data.len();
                self.send_frame(Opcode::Pong, &pong[..len])
            }
            ServerFrame::Other => Ok(()),
        }
    }
}

impl<S: TcpStream> AudioSession for WebSocket<S> {
    type Error = WsError;

    fn send(&mut self, pcm: &[i16]) -> Result<(), WsError> {
        self.send_pcm(pcm)
    }
}

/// [`AudioUplink`] that opens a WebSocket to the server's audio endpoint,
/// locating the server afresh on every attempt.
pub struct WebSocketUplink<'a, C, R> {
    connector: C,
    locator: ServerLocator<'a, R>,
    port: u16,
    path: &'a str,
    config: WsConfig,
}

impl<'a, C, R> WebSocketUplink<'a, C, R>
where
    C: TcpConnector,
    R: HostResolver,
{
    /// Stream to `path` on the located server's `port`.
    pub const fn new(
        connector: C,
        locator: ServerLocator<'a, R>,
        port: u16,
        path: &'a str,
        config: WsConfig,
    ) -> Self {
        Self {
            connector,
            locator,
            port,
            path,
            config,
        }
    }
}

impl<C, R> AudioUplink for WebSocketUplink<'_, C, R>
where
    C: TcpConnector,
    R: HostResolver,
{
    type Session = WebSocket<C::Stream>;
    type Error = WsError;

    fn open(&mut self) -> Result<Self::Session, WsError> {
        let ip = self.locator.locate().ok_or(WsError::Unresolved)?;
        WebSocket::connect(
            &self.connector,
            SocketAddrV4::new(ip, self.port),
            self.locator.host(),
            self.path,
            self.config,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::super::testing::{Recv, ScriptedConnector};
    use super::*;
    use core::net::Ipv4Addr;
    use proptest::prelude::*;
    use std::vec::Vec;

    const ADDR: SocketAddrV4 = SocketAddrV4::new(Ipv4Addr::new(100, 64, 0, 2), 8000);
    const ACCEPT: &[u8] = b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n";

    struct Fixed(Option<Ipv4Addr>);

    impl HostResolver for Fixed {
        fn resolve(&self, _: &str) -> Option<Ipv4Addr> {
            self.0
        }
    }

    /// Decode one masked client frame: (opcode, payload, frame length).
    fn decode_client_frame(bytes: &[u8]) -> (u8, Vec<u8>, usize) {
        assert_eq!(bytes[0] & 0x80, 0x80, "FIN set");
        assert_eq!(bytes[1] & 0x80, 0x80, "client frames must be masked");
        let (len, mut pos) = match bytes[1] & 0x7F {
            126 => (usize::from(u16::from_be_bytes([bytes[2], bytes[3]])), 4),
            127 => (
                usize::try_from(u64::from_be_bytes(bytes[2..10].try_into().unwrap())).unwrap(),
                10,
            ),
            n => (usize::from(n), 2),
        };
        let mask: [u8; 4] = bytes[pos..pos + 4].try_into().unwrap();
        pos += 4;
        let payload = bytes[pos..pos + len]
            .iter()
            .enumerate()
            .map(|(i, b)| b ^ mask[i % 4])
            .collect();
        (bytes[0] & 0x0F, payload, pos + len)
    }

    fn connected() -> (
        ScriptedConnector,
        WebSocket<super::super::testing::ScriptedStream>,
    ) {
        let c = ScriptedConnector::replying(&[ACCEPT]);
        let ws =
            WebSocket::connect(&c, ADDR, "comack-server", "/ws/audio", WsConfig::default()).unwrap();
        c.0.borrow_mut().sent.clear();
        (c, ws)
    }

    #[test]
    fn header_uses_the_shortest_length_encoding() {
        let mut h = [0u8; MAX_HEADER_LEN];
        assert_eq!(encode_client_header(Opcode::Binary, 125, &mut h), 6);
        assert_eq!(&h[..6], &[0x82, 0xFD, 0, 0, 0, 0]);
        assert_eq!(encode_client_header(Opcode::Binary, 3200, &mut h), 8);
        assert_eq!(&h[..8], &[0x82, 0xFE, 0x0C, 0x80, 0, 0, 0, 0]);
        assert_eq!(encode_client_header(Opcode::Pong, 70_000, &mut h), 14);
        assert_eq!(&h[..10], &[0x8A, 0xFF, 0, 0, 0, 0, 0, 0x01, 0x11, 0x70]);
    }

    #[test]
    fn handshake_request_is_well_formed() {
        let mut buf = [0u8; 256];
        let n = write_handshake(&mut buf, "comack-server", 8000, "/ws/audio").unwrap();
        let text = core::str::from_utf8(&buf[..n]).unwrap();
        assert!(text.starts_with("GET /ws/audio HTTP/1.1\r\nHost: comack-server:8000\r\n"));
        assert!(text.contains("Upgrade: websocket\r\n"));
        assert!(text.contains("Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"));
        assert!(text.ends_with("Sec-WebSocket-Version: 13\r\n\r\n"));
        assert_eq!(
            write_handshake(&mut [0u8; 32], "h", 1, "/"),
            Err(WsError::RequestTooLarge)
        );
    }

    #[test]
    fn recognises_the_upgrade_response() {
        assert!(is_handshake_accepted(ACCEPT));
        assert!(is_handshake_accepted(b"HTTP/1.1 101\r\n\r\n"));
        assert!(!is_handshake_accepted(
            b"HTTP/1.1 403 Forbidden\r\nX: 101 \r\n\r\n"
        ));
        assert!(!is_handshake_accepted(b""));
    }

    #[test]
    fn parses_server_control_frames() {
        assert_eq!(
            parse_server_frame(&[0x89, 2, b'h', b'i']),
            ServerFrame::Ping(b"hi")
        );
        assert_eq!(parse_server_frame(&[0x89, 0]), ServerFrame::Ping(b""));
        assert_eq!(
            parse_server_frame(&[0x88, 2, 0x03, 0xE8]),
            ServerFrame::Close
        );
        assert_eq!(
            parse_server_frame(&[0x89, 5, b'h']),
            ServerFrame::Other,
            "truncated ping"
        );
        assert_eq!(
            parse_server_frame(&[0x89, 0x82, 0, 0, 0, 0, 1, 2]),
            ServerFrame::Other,
            "masked"
        );
        assert_eq!(parse_server_frame(&[0x81, 1, b'x']), ServerFrame::Other);
        assert_eq!(parse_server_frame(&[0x89]), ServerFrame::Other);
    }

    #[test]
    fn connect_performs_the_upgrade() {
        let c = ScriptedConnector::replying(&[ACCEPT]);
        assert!(
            WebSocket::connect(&c, ADDR, "comack-server", "/ws/audio", WsConfig::default()).is_ok()
        );
        let wire = c.0.borrow();
        assert_eq!(wire.connects, [(ADDR, Duration::from_secs(5))]);
        assert!(wire.sent.starts_with(b"GET /ws/audio HTTP/1.1\r\n"));
        assert_eq!(wire.recv_timeouts, [Duration::from_secs(5)]);
    }

    #[test]
    fn connect_fails_cleanly() {
        let rejected = ScriptedConnector::replying(&[b"HTTP/1.1 404 Not Found\r\n\r\n"]);
        assert_eq!(
            WebSocket::connect(&rejected, ADDR, "h", "/", WsConfig::default()).err(),
            Some(WsError::HandshakeRejected)
        );
        assert_eq!(rejected.0.borrow().closed, 1, "socket released");

        let silent = ScriptedConnector::default();
        silent.push(Recv::Timeout);
        assert_eq!(
            WebSocket::connect(&silent, ADDR, "h", "/", WsConfig::default()).err(),
            Some(WsError::HandshakeRejected)
        );

        let refused = ScriptedConnector::default();
        refused.0.borrow_mut().refuse_connect = true;
        assert_eq!(
            WebSocket::connect(&refused, ADDR, "h", "/", WsConfig::default()).err(),
            Some(WsError::Connect(NetError::ConnectFailed))
        );
    }

    #[test]
    fn pcm_goes_out_as_one_little_endian_binary_frame() {
        let (c, mut ws) = connected();
        c.push(Recv::Timeout);
        let pcm: Vec<i16> = (0..1600).map(|i| (i * 37 - 20_000) as i16).collect();
        ws.send(&pcm).unwrap();
        let sent = c.sent();
        let (opcode, payload, len) = decode_client_frame(&sent);
        assert_eq!(opcode, 0x2);
        assert_eq!(len, sent.len(), "exactly one frame");
        let expected: Vec<u8> = pcm.iter().flat_map(|s| s.to_le_bytes()).collect();
        assert_eq!(payload, expected);
        assert_eq!(
            c.0.borrow().recv_timeouts.last(),
            Some(&Duration::ZERO),
            "control check polls"
        );
    }

    #[test]
    fn binary_payloads_are_framed() {
        let (c, mut ws) = connected();
        c.push(Recv::Timeout);
        ws.send_binary(b"abc").unwrap();
        let (opcode, payload, _) = decode_client_frame(&c.sent());
        assert_eq!((opcode, payload.as_slice()), (0x2, b"abc".as_slice()));
    }

    #[test]
    fn answers_ping_with_pong() {
        let (c, mut ws) = connected();
        c.push(Recv::Data(std::vec![0x89, 3, 1, 2, 3]));
        ws.send(&[7]).unwrap();
        let sent = c.sent();
        let (_, _, first) = decode_client_frame(&sent);
        let (opcode, payload, _) = decode_client_frame(&sent[first..]);
        assert_eq!(opcode, 0xA);
        assert_eq!(payload, [1, 2, 3]);
    }

    #[test]
    fn close_or_transport_failure_kills_the_session() {
        let (c, mut ws) = connected();
        c.push(Recv::Data(std::vec![0x88, 0]));
        assert_eq!(ws.send(&[1]), Err(WsError::ClosedByServer));

        let (c, mut ws) = connected();
        c.push(Recv::Fail(NetError::Closed));
        assert_eq!(ws.send(&[1]), Err(WsError::Io(NetError::Closed)));

        let (c, mut ws) = connected();
        let next = c.0.borrow().sends;
        c.0.borrow_mut().fail_send_at = Some(next);
        assert_eq!(ws.send(&[1]), Err(WsError::Io(NetError::Io)));
    }

    #[test]
    fn uplink_locates_server_then_connects() {
        let c = ScriptedConnector::replying(&[ACCEPT]);
        let locator = ServerLocator::new(Fixed(Some(*ADDR.ip())), "comack-server");
        let mut uplink =
            WebSocketUplink::new(c.clone(), locator, 8000, "/ws/audio", WsConfig::default());
        assert!(uplink.open().is_ok());
        assert_eq!(c.0.borrow().connects[0].0, ADDR);
        assert!(c
            .sent()
            .windows(23)
            .any(|w| w == b"Host: comack-server:8000\r\n"));
    }

    #[test]
    fn uplink_reports_unresolved_server() {
        let locator = ServerLocator::new(Fixed(None), "comack-server");
        let mut uplink = WebSocketUplink::new(
            ScriptedConnector::default(),
            locator,
            8000,
            "/",
            WsConfig::default(),
        );
        assert_eq!(uplink.open().err(), Some(WsError::Unresolved));
    }

    proptest! {
        #[test]
        fn frames_round_trip(len in 0usize..70_000, opcode in prop_oneof![Just(Opcode::Binary), Just(Opcode::Pong)]) {
            let mut h = [0u8; MAX_HEADER_LEN];
            let n = encode_client_header(opcode, len, &mut h);
            let mut frame = h[..n].to_vec();
            frame.resize(n + len, 0x5A);
            let (op, payload, total) = decode_client_frame(&frame);
            prop_assert_eq!(op, opcode as u8);
            prop_assert_eq!(payload.len(), len);
            prop_assert_eq!(total, frame.len());
        }
    }
}
