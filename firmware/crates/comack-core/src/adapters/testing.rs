//! Scripted doubles for host-side tests: TCP connectors that replay a
//! wire script, plus an in-memory device-config store.

use core::convert::Infallible;
use core::net::SocketAddrV4;
use core::time::Duration;
use std::cell::RefCell;
use std::collections::VecDeque;
use std::rc::Rc;
use std::vec::Vec;

use super::net::{NetError, TcpConnector, TcpStream};
use crate::application::ports::DeviceConfigStore;
use crate::domain::DeviceConfig;

/// What one `recv` call returns.
#[derive(Clone, Debug)]
pub enum Recv {
    Data(Vec<u8>),
    Timeout,
    Fail(NetError),
}

/// Everything a scripted connection saw, shared with the test.
#[derive(Debug, Default)]
pub struct Wire {
    pub connects: Vec<(SocketAddrV4, Duration)>,
    pub sent: Vec<u8>,
    pub recv_timeouts: Vec<Duration>,
    pub closed: u32,
    /// Fail the nth `send_all` call (0-based) across all connections.
    pub fail_send_at: Option<usize>,
    pub sends: usize,
    pub refuse_connect: bool,
    /// Replies for each recv; an exhausted script reports `Closed`.
    pub script: VecDeque<Recv>,
}

#[derive(Clone, Default)]
pub struct ScriptedConnector(pub Rc<RefCell<Wire>>);

impl ScriptedConnector {
    pub fn replying(chunks: &[&[u8]]) -> Self {
        let c = Self::default();
        c.0.borrow_mut()
            .script
            .extend(chunks.iter().map(|b| Recv::Data(b.to_vec())));
        c
    }

    pub fn push(&self, recv: Recv) {
        self.0.borrow_mut().script.push_back(recv);
    }

    pub fn sent(&self) -> Vec<u8> {
        self.0.borrow().sent.clone()
    }
}

pub struct ScriptedStream(Rc<RefCell<Wire>>);

impl TcpConnector for ScriptedConnector {
    type Stream = ScriptedStream;

    fn connect(&self, addr: SocketAddrV4, timeout: Duration) -> Result<ScriptedStream, NetError> {
        let mut w = self.0.borrow_mut();
        w.connects.push((addr, timeout));
        if w.refuse_connect {
            return Err(NetError::ConnectFailed);
        }
        Ok(ScriptedStream(self.0.clone()))
    }
}

impl TcpStream for ScriptedStream {
    fn send_all(&mut self, data: &[u8]) -> Result<(), NetError> {
        let mut w = self.0.borrow_mut();
        let index = w.sends;
        w.sends += 1;
        if w.fail_send_at == Some(index) {
            return Err(NetError::Io);
        }
        w.sent.extend_from_slice(data);
        Ok(())
    }

    fn recv(&mut self, buffer: &mut [u8], timeout: Duration) -> Result<usize, NetError> {
        let mut w = self.0.borrow_mut();
        w.recv_timeouts.push(timeout);
        match w.script.pop_front() {
            None => Err(NetError::Closed),
            Some(Recv::Timeout) => Ok(0),
            Some(Recv::Fail(e)) => Err(e),
            Some(Recv::Data(mut data)) => {
                let n = data.len().min(buffer.len());
                buffer[..n].copy_from_slice(&data[..n]);
                if n < data.len() {
                    w.script.push_front(Recv::Data(data.split_off(n)));
                }
                Ok(n)
            }
        }
    }
}

impl Drop for ScriptedStream {
    fn drop(&mut self) {
        self.0.borrow_mut().closed += 1;
    }
}

/// In-memory [`DeviceConfigStore`] double: keeps the last saved
/// configuration in place, nothing touches hardware.
#[derive(Debug, Default)]
pub struct InMemoryConfigStore {
    config: Option<DeviceConfig>,
}

impl DeviceConfigStore for InMemoryConfigStore {
    type Error = Infallible;

    fn load(&self) -> Result<Option<DeviceConfig>, Self::Error> {
        Ok(self.config.clone())
    }

    fn save(&mut self, config: &DeviceConfig) -> Result<(), Self::Error> {
        self.config = Some(config.clone());
        Ok(())
    }

    fn clear(&mut self) -> Result<(), Self::Error> {
        self.config = None;
        Ok(())
    }
}
