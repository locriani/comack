//! The tailnet, via the vendored MicroLink C library: Tailscale
//! registration, MagicDNS lookups and TCP sockets tunnelled over WireGuard.
//!
//! This is the only module that touches MicroLink; the rest of the firmware
//! sees it through the core's [`TcpConnector`] and [`HostResolver`] ports.

use core::ffi::{c_void, CStr};
use core::net::{Ipv4Addr, SocketAddrV4};
use core::ptr::{self, NonNull};
use core::time::Duration;
use std::ffi::CString;

use anyhow::{bail, Result};
use comack_core::adapters::{HostResolver, NetError, TcpConnector, TcpStream};
use esp_idf_svc::sys::microlink as ml;
use esp_idf_svc::sys::{esp, ESP_OK};
use log::info;

use crate::latch::Latch;

/// Registration settings. The strings must be `'static`: MicroLink keeps
/// the pointers, not copies.
pub struct TailnetConfig {
    pub auth_key: &'static CStr,
    pub device_name: &'static CStr,
    pub max_peers: u8,
    pub wifi_tx_power_dbm: i8,
}

/// MicroLink's connection states, mirroring `microlink_state_t`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum State {
    Idle,
    WifiWait,
    Connecting,
    Registering,
    Connected,
    Reconnecting,
    Error,
}

impl State {
    fn from_raw(raw: u32) -> Option<Self> {
        Some(match raw {
            0 => Self::Idle,
            1 => Self::WifiWait,
            2 => Self::Connecting,
            3 => Self::Registering,
            4 => Self::Connected,
            5 => Self::Reconnecting,
            6 => Self::Error,
            _ => return None,
        })
    }
}

/// Handle to the running MicroLink client. Cheap to copy; MicroLink is
/// queue-based and safe to call from several tasks.
#[derive(Clone, Copy)]
pub struct Tailnet {
    handle: NonNull<ml::microlink_t>,
    registered: &'static Latch,
}

// SAFETY: the handle is only passed to MicroLink's thread-safe public API,
// and the instance is never destroyed.
unsafe impl Send for Tailnet {}
// SAFETY: as above; `&self` methods only call the thread-safe API.
unsafe impl Sync for Tailnet {}

impl Tailnet {
    /// Initialise MicroLink and start registering. Wi-Fi must be up.
    pub fn start(config: &TailnetConfig) -> Result<Self> {
        // SAFETY: plain C struct of integers, bools and pointers; all-zero
        // is its documented "use defaults" state.
        let mut c: ml::microlink_config_t = unsafe { core::mem::zeroed() };
        c.auth_key = config.auth_key.as_ptr();
        c.device_name = config.device_name.as_ptr();
        c.enable_derp = true;
        c.enable_stun = true;
        c.enable_disco = true;
        c.max_peers = config.max_peers;
        c.wifi_tx_power_dbm = config.wifi_tx_power_dbm;

        // SAFETY: `c` is valid for the call and its strings are 'static.
        let Some(handle) = NonNull::new(unsafe { ml::microlink_init(&c) }) else {
            bail!("microlink_init failed");
        };
        let registered: &'static Latch = Box::leak(Box::new(Latch::new()));
        // SAFETY: valid handle; `registered` outlives MicroLink (leaked).
        unsafe {
            ml::microlink_set_state_callback(
                handle.as_ptr(),
                Some(on_state),
                ptr::from_ref(registered).cast_mut().cast(),
            );
            ml::microlink_set_peer_callback(handle.as_ptr(), Some(on_peer), ptr::null_mut());
        }
        // SAFETY: valid handle.
        esp!(unsafe { ml::microlink_start(handle.as_ptr()) })?;
        Ok(Self { handle, registered })
    }

    /// Whether the node has registered with the tailnet — for bounded
    /// waits that must stay responsive to the setup gesture.
    pub fn registered(&self) -> &crate::latch::Latch {
        self.registered
    }
}

fn ip_from_raw(raw: u32) -> Option<Ipv4Addr> {
    // MicroLink uses host-order integers with the first octet in the top byte.
    (raw != 0).then(|| Ipv4Addr::from(raw))
}

/// Duration as MicroLink milliseconds, at least 1: zero means "default"
/// to MicroLink, not "poll".
fn timeout_ms(timeout: Duration) -> u32 {
    u32::try_from(timeout.as_millis())
        .unwrap_or(u32::MAX)
        .max(1)
}

extern "C" fn on_state(
    handle: *mut ml::microlink_t,
    state: ml::microlink_state_t,
    user: *mut c_void,
) {
    #[allow(clippy::useless_conversion)] // the enum's C type varies by target
    let state = u32::try_from(state).ok().and_then(State::from_raw);
    info!("Tailscale state -> {state:?}");
    if state == Some(State::Connected) {
        // SAFETY: MicroLink passes back the handle it was created with.
        let ip = ip_from_raw(unsafe { ml::microlink_get_vpn_ip(handle) });
        info!("Tailscale registered; node IP {ip:?}");
        // SAFETY: `user` is the leaked `Latch` registered in `start`.
        let registered = unsafe { &*user.cast::<Latch>() };
        registered.set();
    }
}

extern "C" fn on_peer(
    _: *mut ml::microlink_t,
    peer: *const ml::microlink_peer_info_t,
    _: *mut c_void,
) {
    // SAFETY: MicroLink passes a valid snapshot for the duration of the call.
    let Some(peer) = (unsafe { peer.as_ref() }) else {
        return;
    };
    // SAFETY: MicroLink NUL-terminates the fixed-size hostname buffer.
    let name = unsafe { CStr::from_ptr(peer.hostname.as_ptr()) }.to_string_lossy();
    info!(
        "Tailscale peer: {name} ({:?}) online={} direct={}",
        ip_from_raw(peer.vpn_ip),
        peer.online,
        peer.direct_path
    );
}

impl HostResolver for Tailnet {
    /// MagicDNS lookup against the peer list (no network traffic).
    fn resolve(&self, host: &str) -> Option<Ipv4Addr> {
        let host = CString::new(host).ok()?;
        // SAFETY: valid handle and NUL-terminated string.
        ip_from_raw(unsafe { ml::microlink_resolve(self.handle.as_ptr(), host.as_ptr()) })
    }
}

/// A TCP connection through the tunnel. Dropping it closes the socket.
pub struct TailnetStream(NonNull<ml::microlink_tcp_socket_t>);

// SAFETY: the socket is owned by exactly one `TailnetStream`, and MicroLink
// sockets are plain lwIP sockets usable from any task.
unsafe impl Send for TailnetStream {}

impl TcpConnector for Tailnet {
    type Stream = TailnetStream;

    fn connect(&self, addr: SocketAddrV4, timeout: Duration) -> Result<TailnetStream, NetError> {
        // SAFETY: valid handle.
        let socket = unsafe {
            ml::microlink_tcp_connect(
                self.handle.as_ptr(),
                u32::from(*addr.ip()),
                addr.port(),
                timeout_ms(timeout),
            )
        };
        NonNull::new(socket)
            .map(TailnetStream)
            .ok_or(NetError::ConnectFailed)
    }
}

impl TcpStream for TailnetStream {
    fn send_all(&mut self, data: &[u8]) -> Result<(), NetError> {
        if data.is_empty() {
            return Ok(()); // MicroLink rejects zero-length sends
        }
        // SAFETY: open socket; `data` is valid for `len` bytes. MicroLink
        // gives up after 5 s of a stalled peer.
        let err =
            unsafe { ml::microlink_tcp_send(self.0.as_ptr(), data.as_ptr().cast(), data.len()) };
        if err == ESP_OK as _ {
            Ok(())
        } else {
            Err(NetError::Io)
        }
    }

    fn recv(&mut self, buffer: &mut [u8], timeout: Duration) -> Result<usize, NetError> {
        // SAFETY: open socket; `buffer` is valid for writes of its length.
        let n = unsafe {
            ml::microlink_tcp_recv(
                self.0.as_ptr(),
                buffer.as_mut_ptr().cast(),
                buffer.len(),
                timeout_ms(timeout),
            )
        };
        // >0 bytes, 0 timeout, -1 error or peer closed.
        usize::try_from(n).map_err(|_| NetError::Closed)
    }
}

impl Drop for TailnetStream {
    fn drop(&mut self) {
        // SAFETY: we own the socket and never use it again.
        unsafe { ml::microlink_tcp_close(self.0.as_ptr()) }
    }
}
