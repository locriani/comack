//! The TCP boundary the protocol adapters are written against.
//!
//! On the device it is implemented by MicroLink sockets tunnelled through
//! Tailscale; on the desktop by `std::net`.

use core::net::{Ipv4Addr, SocketAddrV4};
use core::time::Duration;

/// Transport failures.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NetError {
    /// The connection could not be established.
    ConnectFailed,
    /// The peer closed the connection.
    Closed,
    /// Any other transport error; the connection is unusable.
    Io,
}

/// A connected TCP stream. Dropping it closes the connection.
pub trait TcpStream {
    /// Send all of `data`, blocking until it is written.
    fn send_all(&mut self, data: &[u8]) -> Result<(), NetError>;

    /// Receive up to `buffer.len()` bytes, waiting at most `timeout`
    /// (a zero timeout polls). `Ok(0)` means nothing arrived in time.
    fn recv(&mut self, buffer: &mut [u8], timeout: Duration) -> Result<usize, NetError>;
}

/// Opens TCP connections.
pub trait TcpConnector {
    /// The stream type produced.
    type Stream: TcpStream;

    /// Connect to `addr`, giving up after `timeout`.
    fn connect(&self, addr: SocketAddrV4, timeout: Duration) -> Result<Self::Stream, NetError>;
}

/// Maps host names to IPv4 addresses (Tailscale MagicDNS on the device).
pub trait HostResolver {
    /// The address of `host`, if known.
    fn resolve(&self, host: &str) -> Option<Ipv4Addr>;
}

/// Finds the server: by name first, then as a literal dotted-quad address.
#[derive(Clone, Copy, Debug)]
pub struct ServerLocator<'a, R> {
    resolver: R,
    host: &'a str,
}

impl<'a, R: HostResolver> ServerLocator<'a, R> {
    /// Locate `host` using `resolver`.
    pub const fn new(resolver: R, host: &'a str) -> Self {
        Self { resolver, host }
    }

    /// The configured host name.
    pub const fn host(&self) -> &'a str {
        self.host
    }

    /// The server's address, if it can be determined yet.
    pub fn locate(&self) -> Option<Ipv4Addr> {
        self.resolver
            .resolve(self.host)
            .or_else(|| self.host.parse().ok())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct OnlyKnows(&'static str, Ipv4Addr);

    impl HostResolver for OnlyKnows {
        fn resolve(&self, host: &str) -> Option<Ipv4Addr> {
            (host == self.0).then_some(self.1)
        }
    }

    const PEER: Ipv4Addr = Ipv4Addr::new(100, 64, 0, 7);

    #[test]
    fn resolves_by_name() {
        let locator = ServerLocator::new(OnlyKnows("localhost", PEER), "localhost");
        assert_eq!(locator.locate(), Some(PEER));
        assert_eq!(locator.host(), "localhost");
    }

    #[test]
    fn falls_back_to_literal_address() {
        let locator = ServerLocator::new(OnlyKnows("localhost", PEER), "100.101.102.103");
        assert_eq!(locator.locate(), Some(Ipv4Addr::new(100, 101, 102, 103)));
    }

    #[test]
    fn unknown_name_is_unresolved() {
        let locator = ServerLocator::new(OnlyKnows("localhost", PEER), "laptop");
        assert_eq!(locator.locate(), None);
    }
}
