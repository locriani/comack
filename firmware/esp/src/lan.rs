//! LAN TCP and name resolution over `std::net` (lwIP underneath ESP-IDF's
//! `std`), for the LAN-only mode: an empty Tailscale key is a legitimate
//! setup, and MicroLink cannot serve as the transport without one — its
//! sockets exist only once a tailnet is joined.
//!
//! These adapters mirror the simulator's `comack_sim::net` types; they are
//! deliberately not shared: the simulator is a desktop development tool,
//! and threading it into the firmware's dependency tree to reuse two small
//! trait impls would couple the two binaries for no behavioural gain.

use std::io::{ErrorKind, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, SocketAddrV4, TcpStream as StdTcpStream, ToSocketAddrs};
use std::time::Duration;

use comack_core::adapters::{HostResolver, NetError, TcpConnector, TcpStream};

/// Connects with `std::net::TcpStream` — plain LAN, no tailnet.
#[derive(Clone, Copy, Debug, Default)]
pub struct LanConnector;

/// A connected `std` socket.
#[derive(Debug)]
pub struct LanStream(StdTcpStream);

impl TcpConnector for LanConnector {
    type Stream = LanStream;

    fn connect(&self, addr: SocketAddrV4, timeout: Duration) -> Result<LanStream, NetError> {
        let stream = StdTcpStream::connect_timeout(
            &SocketAddr::V4(addr),
            timeout.max(Duration::from_millis(1)),
        )
        .map_err(|_| NetError::ConnectFailed)?;
        stream.set_nodelay(true).map_err(|_| NetError::Io)?;
        Ok(LanStream(stream))
    }
}

impl TcpStream for LanStream {
    fn send_all(&mut self, data: &[u8]) -> Result<(), NetError> {
        self.0.write_all(data).map_err(|e| match e.kind() {
            ErrorKind::BrokenPipe | ErrorKind::ConnectionReset | ErrorKind::ConnectionAborted => {
                NetError::Closed
            }
            _ => NetError::Io,
        })
    }

    fn recv(&mut self, buffer: &mut [u8], timeout: Duration) -> Result<usize, NetError> {
        // `std` rejects a zero read timeout; poll for a millisecond instead.
        self.0
            .set_read_timeout(Some(timeout.max(Duration::from_millis(1))))
            .map_err(|_| NetError::Io)?;
        match self.0.read(buffer) {
            Ok(0) if !buffer.is_empty() => Err(NetError::Closed),
            Ok(n) => Ok(n),
            Err(e) if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) => Ok(0),
            Err(e)
                if matches!(
                    e.kind(),
                    ErrorKind::ConnectionReset | ErrorKind::ConnectionAborted
                ) =>
            {
                Err(NetError::Closed)
            }
            Err(_) => Err(NetError::Io),
        }
    }
}

/// Resolves names with lwIP's resolver (first IPv4 result); ServerLocator
/// tries literal dotted-quad hosts before consulting this.
#[derive(Clone, Copy, Debug, Default)]
pub struct LanResolver;

impl HostResolver for LanResolver {
    fn resolve(&self, host: &str) -> Option<Ipv4Addr> {
        (host, 0)
            .to_socket_addrs()
            .ok()?
            .find_map(|addr| match addr {
                SocketAddr::V4(v4) => Some(*v4.ip()),
                SocketAddr::V6(_) => None,
            })
    }
}
