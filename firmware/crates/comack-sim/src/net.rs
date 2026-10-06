//! TCP and name resolution over `std::net`.

use std::io::{ErrorKind, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, SocketAddrV4, TcpStream as StdTcpStream, ToSocketAddrs};
use std::time::Duration;

use comack_core::adapters::{HostResolver, NetError, TcpConnector, TcpStream};

/// Connects with `std::net::TcpStream`.
#[derive(Clone, Copy, Debug, Default)]
pub struct StdConnector;

/// A connected `std` socket.
#[derive(Debug)]
pub struct StdStream(StdTcpStream);

impl TcpConnector for StdConnector {
    type Stream = StdStream;

    fn connect(&self, addr: SocketAddrV4, timeout: Duration) -> Result<StdStream, NetError> {
        let stream = StdTcpStream::connect_timeout(
            &SocketAddr::V4(addr),
            timeout.max(Duration::from_millis(1)),
        )
        .map_err(|_| NetError::ConnectFailed)?;
        stream.set_nodelay(true).map_err(|_| NetError::Io)?;
        Ok(StdStream(stream))
    }
}

impl TcpStream for StdStream {
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

/// Resolves names with the operating system's resolver (first IPv4 result).
#[derive(Clone, Copy, Debug, Default)]
pub struct SystemResolver;

impl HostResolver for SystemResolver {
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::thread;

    fn listener() -> (TcpListener, SocketAddrV4) {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let SocketAddr::V4(addr) = l.local_addr().unwrap() else {
            unreachable!()
        };
        (l, addr)
    }

    #[test]
    fn round_trips_bytes() {
        let (l, addr) = listener();
        let server = thread::spawn(move || {
            let (mut s, _) = l.accept().unwrap();
            let mut buf = [0u8; 5];
            s.read_exact(&mut buf).unwrap();
            s.write_all(&buf).unwrap();
        });
        let mut stream = StdConnector.connect(addr, Duration::from_secs(1)).unwrap();
        stream.send_all(b"hello").unwrap();
        let mut got = Vec::new();
        while got.len() < 5 {
            let mut buf = [0u8; 8];
            let n = stream.recv(&mut buf, Duration::from_secs(1)).unwrap();
            got.extend_from_slice(&buf[..n]);
        }
        assert_eq!(got, b"hello");
        server.join().unwrap();
    }

    #[test]
    fn silence_is_a_timeout_not_an_error() {
        let (l, addr) = listener();
        let mut stream = StdConnector.connect(addr, Duration::from_secs(1)).unwrap();
        let _peer = l.accept().unwrap();
        let mut buf = [0u8; 4];
        assert_eq!(stream.recv(&mut buf, Duration::ZERO), Ok(0));
        assert_eq!(stream.recv(&mut buf, Duration::from_millis(20)), Ok(0));
    }

    #[test]
    fn peer_close_is_reported() {
        let (l, addr) = listener();
        let mut stream = StdConnector.connect(addr, Duration::from_secs(1)).unwrap();
        drop(l.accept().unwrap());
        let mut buf = [0u8; 4];
        assert_eq!(
            stream.recv(&mut buf, Duration::from_secs(1)),
            Err(NetError::Closed)
        );
    }

    #[test]
    fn refused_connection_fails() {
        let (l, addr) = listener();
        drop(l);
        assert_eq!(
            StdConnector.connect(addr, Duration::from_secs(1)).err(),
            Some(NetError::ConnectFailed)
        );
    }

    #[test]
    fn resolves_localhost_and_literals() {
        assert_eq!(
            SystemResolver.resolve("127.0.0.1"),
            Some(Ipv4Addr::LOCALHOST)
        );
        assert_eq!(
            SystemResolver.resolve("localhost"),
            Some(Ipv4Addr::LOCALHOST)
        );
        assert_eq!(SystemResolver.resolve("no-such-host.invalid"), None);
    }
}
