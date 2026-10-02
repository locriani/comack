//! An in-process stand-in for the Comack server (`server/main.py`), speaking
//! real HTTP/1.1 and WebSocket over loopback.

#![allow(dead_code, clippy::unused_self)]

use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant};

use comack_core::domain::FRAME_BYTES;

#[derive(Default)]
pub struct State {
    /// Published frames by issue number.
    pub issues: BTreeMap<u32, Vec<u8>>,
    /// The issue `/api/comics/current` reports.
    pub current: Option<u32>,
    pub triggers: u32,
    /// Publish and reveal a new issue whenever the trigger is posted.
    pub reveal_on_trigger: bool,
    pub requests: Vec<String>,

    pub ws_sessions: u32,
    /// Concatenated binary payloads received on `/ws/audio`.
    pub audio: Vec<u8>,
    pub audio_frames: usize,
    pub pongs: Vec<Vec<u8>>,
    /// After this many audio frames (global count), send a ping.
    pub ping_after: Option<usize>,
    /// After this many audio frames (global count), send close and hang up.
    pub close_after: Option<usize>,
    pub unmasked_frames: usize,
}

/// A frame whose every byte is the issue number, so tests can tell frames apart.
pub fn frame_for(issue: u32) -> Vec<u8> {
    vec![u8::try_from(issue).unwrap(); FRAME_BYTES]
}

pub struct FakeServer {
    pub port: u16,
    state: Arc<Mutex<State>>,
}

impl FakeServer {
    pub fn start(setup: impl FnOnce(&mut State)) -> Self {
        let mut state = State::default();
        setup(&mut state);
        let state = Arc::new(Mutex::new(state));
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let shared = state.clone();
        thread::spawn(move || {
            for conn in listener.incoming() {
                let Ok(conn) = conn else { continue };
                let state = shared.clone();
                thread::spawn(move || handle(conn, &state));
            }
        });
        Self { port, state }
    }

    pub fn ip(&self) -> Ipv4Addr {
        Ipv4Addr::LOCALHOST
    }

    pub fn state(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap()
    }

    /// Wait until `done` holds, failing the test after `timeout`.
    pub fn wait_for(&self, timeout: Duration, mut done: impl FnMut(&State) -> bool) {
        let deadline = Instant::now() + timeout;
        while !done(&self.state()) {
            assert!(
                Instant::now() < deadline,
                "timed out waiting for the fake server"
            );
            thread::sleep(Duration::from_millis(5));
        }
    }
}

fn read_head(conn: &mut TcpStream) -> Option<String> {
    let mut head = Vec::new();
    let mut byte = [0u8; 1];
    while !head.ends_with(b"\r\n\r\n") {
        if conn.read(&mut byte).ok()? == 0 {
            return None;
        }
        head.push(byte[0]);
    }
    String::from_utf8(head).ok()
}

fn respond(conn: &mut TcpStream, status: &str, body: &[u8]) {
    let head = format!(
        "HTTP/1.1 {status}\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
        body.len()
    );
    let _ = conn.write_all(head.as_bytes());
    let _ = conn.write_all(body);
}

fn handle(mut conn: TcpStream, state: &Mutex<State>) {
    let Some(head) = read_head(&mut conn) else {
        return;
    };
    let request_line = head.lines().next().unwrap_or_default().to_owned();
    state.lock().unwrap().requests.push(request_line.clone());
    let mut parts = request_line.split(' ');
    let (method, path) = (
        parts.next().unwrap_or_default(),
        parts.next().unwrap_or_default(),
    );

    match (method, path) {
        ("GET", "/ws/audio") if head.to_ascii_lowercase().contains("upgrade: websocket") => {
            serve_websocket(conn, state);
        }
        ("GET", "/api/comics/current") => {
            let current = state.lock().unwrap().current;
            match current {
                Some(n) => respond(
                    &mut conn,
                    "200 OK",
                    format!(r#"{{"id":1,"issue_number":{n},"series_issue":1}}"#).as_bytes(),
                ),
                None => respond(
                    &mut conn,
                    "404 Not Found",
                    br#"{"detail":"No displayed comic yet"}"#,
                ),
            }
        }
        ("POST", "/api/trigger") => {
            let mut s = state.lock().unwrap();
            s.triggers += 1;
            if s.reveal_on_trigger {
                let next = s.issues.keys().last().copied().unwrap_or(0) + 1;
                s.issues.insert(next, frame_for(next));
                s.current = Some(next);
            }
            drop(s);
            respond(&mut conn, "200 OK", br#"{"status":"ok"}"#);
        }
        ("GET", p) if p.starts_with("/api/comics/") && p.ends_with("/bin") => {
            let n: Option<u32> = p["/api/comics/".len()..p.len() - "/bin".len()].parse().ok();
            let frame = n.and_then(|n| state.lock().unwrap().issues.get(&n).cloned());
            match frame {
                Some(frame) => respond(&mut conn, "200 OK", &frame),
                None => respond(&mut conn, "404 Not Found", br#"{"detail":"BIN not found"}"#),
            }
        }
        _ => respond(&mut conn, "404 Not Found", b"{}"),
    }
}

fn serve_websocket(mut conn: TcpStream, state: &Mutex<State>) {
    state.lock().unwrap().ws_sessions += 1;
    let _ = conn.write_all(
        b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\
          Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n\r\n",
    );
    loop {
        let mut head = [0u8; 2];
        if conn.read_exact(&mut head).is_err() {
            return;
        }
        let opcode = head[0] & 0x0F;
        let masked = head[1] & 0x80 != 0;
        let len = match head[1] & 0x7F {
            126 => {
                let mut ext = [0u8; 2];
                conn.read_exact(&mut ext).unwrap();
                usize::from(u16::from_be_bytes(ext))
            }
            127 => {
                let mut ext = [0u8; 8];
                conn.read_exact(&mut ext).unwrap();
                usize::try_from(u64::from_be_bytes(ext)).unwrap()
            }
            n => usize::from(n),
        };
        let mut mask = [0u8; 4];
        if masked {
            conn.read_exact(&mut mask).unwrap();
        }
        let mut payload = vec![0u8; len];
        if conn.read_exact(&mut payload).is_err() {
            return;
        }
        for (i, b) in payload.iter_mut().enumerate() {
            *b ^= mask[i % 4];
        }

        let mut s = state.lock().unwrap();
        if !masked {
            s.unmasked_frames += 1;
        }
        match opcode {
            0x2 => {
                s.audio.extend_from_slice(&payload);
                s.audio_frames += 1;
                let frames = s.audio_frames;
                if s.ping_after == Some(frames) {
                    let _ = conn.write_all(&[0x89, 4, b'p', b'i', b'n', b'g']);
                }
                if s.close_after == Some(frames) {
                    let _ = conn.write_all(&[0x88, 2, 0x03, 0xE8]);
                    return;
                }
            }
            0xA => s.pongs.push(payload),
            0x8 => return,
            _ => {}
        }
    }
}

/// Address for tests that need a port nothing listens on.
pub fn closed_port() -> u16 {
    let l = TcpListener::bind("127.0.0.1:0").unwrap();
    let SocketAddr::V4(addr) = l.local_addr().unwrap() else {
        unreachable!()
    };
    addr.port()
}
