//! The provisioning portal served over std TCP, for end-to-end tests.
//!
//! It runs the same [`route`] decision as the device's SoftAP server —
//! form, validation, and store writes all come from `comack-core` — so a
//! loopback test exercises the exact logic that ships.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};

use comack_core::adapters::http::Method;
use comack_core::adapters::portal::route;
use comack_core::application::ports::DeviceConfigStore;

/// Serves the portal on a random loopback port, one thread per connection.
/// Threads run until the test process exits; nothing here needs shutdown.
pub struct ProvisioningServer {
    /// The loopback port to point a client at.
    pub port: u16,
    _accept_loop: JoinHandle<()>,
}

impl ProvisioningServer {
    /// Binds a loopback port and serves the portal from `store`.
    ///
    /// # Panics
    ///
    /// Panics if the loopback bind or thread spawn fails; tests want to
    /// fail loudly rather than report a vacuous pass.
    pub fn start<S>(store: Arc<Mutex<S>>) -> Self
    where
        S: DeviceConfigStore + Send + 'static,
    {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind a loopback port");
        let port = listener.local_addr().expect("bound address").port();
        let accept_loop = thread::Builder::new()
            .name("provisioning-accept".into())
            .spawn(move || {
                for connection in listener.incoming() {
                    let Ok(connection) = connection else {
                        continue;
                    };
                    let store = store.clone();
                    let _ = thread::Builder::new()
                        .name("provisioning-conn".into())
                        .spawn(move || handle(connection, &store));
                }
            })
            .expect("spawn the accept loop");
        Self {
            port,
            _accept_loop: accept_loop,
        }
    }
}

/// Reads one HTTP request, applies the shared `route`, writes the page.
fn handle<S: DeviceConfigStore>(mut connection: TcpStream, store: &Arc<Mutex<S>>) {
    let Some(request) = read_request(&mut connection) else {
        return;
    };
    // Method::Get/Post cover the portal; anything else routes to 404.
    let (status, body) = route(
        &mut *store
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner),
        request.method,
        &request.path,
        &request.body,
    );
    let reason = match status {
        200 => "OK",
        400 => "Bad Request",
        404 => "Not Found",
        _ => "Internal Server Error",
    };
    let response = format!(
        "HTTP/1.1 {status} {reason}\r\ncontent-type: text/html\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
        body.len()
    );
    let _ = connection.write_all(response.as_bytes());
    let _ = connection.flush();
}

/// One parsed request: method, path, and (for POSTs) the body.
struct ParsedRequest {
    method: Method,
    path: String,
    body: Vec<u8>,
}

/// Largest body accepted; the form is a few hundred bytes.
const BODY_MAX: usize = 8192;

/// Reads the request head and its content-length-framed body.
fn read_request(connection: &mut TcpStream) -> Option<ParsedRequest> {
    // Read until the end of the head, then the body per Content-Length.
    let mut head = Vec::new();
    let mut byte = [0u8; 1];
    loop {
        match connection.read(&mut byte) {
            // EOF or an error both mean the request never arrived.
            Ok(0) | Err(_) => return None,
            Ok(_) => {
                head.push(byte[0]);
                if head.ends_with(b"\r\n\r\n") {
                    break;
                }
                if head.len() > 16 * 1024 {
                    return None;
                }
            }
        }
    }

    let head = String::from_utf8(head).ok()?;
    let mut lines = head.lines();
    let request_line = lines.next()?;
    let mut parts = request_line.split_whitespace();
    let method = match parts.next()? {
        "POST" => Method::Post,
        // Anything else — including GET — serves the form.
        _ => Method::Get,
    };
    let path = parts.next()?.to_string();

    let content_length = head
        .lines()
        .find_map(|line| {
            let (name, value) = line.split_once(':')?;
            name.trim()
                .eq_ignore_ascii_case("content-length")
                .then(|| value.trim().parse::<usize>().ok())?
        })
        .unwrap_or(0)
        .min(BODY_MAX);

    let mut body = vec![0u8; content_length];
    connection.read_exact(&mut body).ok()?;

    Some(ParsedRequest { method, path, body })
}
