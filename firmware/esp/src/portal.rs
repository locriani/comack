//! The on-device provisioning portal: a `comack-setup` access point with
//! the setup form served over plain HTTP. The AP is WPA2 with a random
//! password shown on the e-paper (and in a join QR code).
//!
//! The pages, form decoding, validation and the store write all come from
//! the core's [`route`] — the same decision the simulator's loopback
//! server runs — so this file is only the transport: AP mode, one
//! connection at a time, request framing, and the screens between them.

use std::io::{ErrorKind, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, PoisonError};
use std::thread;
use std::time::Duration;

use anyhow::{Context, Result};
use comack_core::adapters::http::Method;
use comack_core::adapters::portal::route;
use comack_core::application::ports::{DeviceConfigStore, FrameDisplay};
use comack_core::domain::{generate_setup_password, SetupPassword};
use esp_idf_svc::wifi::{
    AccessPointConfiguration, AuthMethod, BlockingWifi, ClientConfiguration, Configuration, EspWifi,
};
use log::{info, warn};

use crate::screens;

/// The network the portal broadcasts; the instructions screen tells
/// people to join it.
pub const AP_SSID: &str = "comack-setup";

/// The port the portal serves on: 80, so the URL needs no explicit port
/// on a phone.
const PORT: u16 = 80;

/// Per-connection read timeout: a phone that opens a socket and stalls
/// must not wedge the portal.
/// Kept short: after a save, the portal waits out any still-idle sockets.
const READ_TIMEOUT: Duration = Duration::from_secs(5);

/// Stack of a connection-handler thread: the request buffers live on the
/// heap, so this only covers `route` and response formatting.
const HANDLER_STACK: usize = 8192;

/// Largest accepted request head+body; the form is a few hundred bytes.
const REQUEST_MAX: usize = 16 * 1024;

/// Runs the portal until one submission saves: AP up, form served, config
/// stored. Leaves the Wi-Fi stack stopped, ready for station mode — the
/// caller hands it to [`crate::wifi::supervise_networks`] afterwards.
///
/// # Errors
/// Propagates Wi-Fi and socket failures, and screen errors — all fatal
/// for this attempt (the device retries on the next boot or button hold).
pub fn run<S, D>(
    wifi: &mut BlockingWifi<EspWifi<'static>>,
    display: &mut D,
    frame: &mut comack_core::domain::Frame,
    store: &mut S,
) -> Result<()>
where
    S: DeviceConfigStore + Send,
    D: FrameDisplay,
{
    let password = fresh_password(wifi)?;

    wifi.set_configuration(&access_point_configuration(AP_SSID, &password)?)
        .context("switching to access-point mode")?;
    wifi.start().context("starting the access point")?;
    // The driver's start() only requests startup; poll until the AP
    // interface is actually up so the HTTP listener has an IP to bind.
    while !matches!(wifi.is_up(), Ok(true)) {
        thread::sleep(Duration::from_millis(50));
    }

    // esp-idf-svc gives the AP 192.168.71.1, not lwIP's usual 192.168.4.1,
    // so ask the interface rather than printing an address that is wrong.
    let ip = wifi
        .wifi()
        .ap_netif()
        .get_ip_info()
        .context("reading the access point address")?
        .ip;
    let url = format!("http://{ip}");

    // The 30 s e-paper refresh runs with the AP already serving.
    screens::instructions(display, frame, AP_SSID, &password, &url)
        .map_err(|e| anyhow::anyhow!("instructions screen: {e:?}"))?;

    let listener = TcpListener::bind(("0.0.0.0", PORT)).context("binding the portal port")?;
    info!("Portal AP '{AP_SSID}' up; open {url}");

    serve(&listener, store)?;
    screens::saved(display, frame).map_err(|e| anyhow::anyhow!("saved screen: {e:?}"))?;

    // Hand the radio back: the supervisor reconfigures it for station mode.
    wifi.stop().context("stopping the access point")?;
    info!("Configuration saved; portal closed.");
    Ok(())
}

/// A new random setup password. `esp_random` is only a true random source
/// while the radio runs, so the stack is started (station mode, idle) for
/// the draw and stopped again, leaving it ready for AP mode.
fn fresh_password(wifi: &mut BlockingWifi<EspWifi<'static>>) -> Result<SetupPassword> {
    wifi.set_configuration(&Configuration::Client(ClientConfiguration::default()))
        .context("starting the radio for entropy")?;
    wifi.start().context("starting the radio for entropy")?;
    // SAFETY: `esp_random` has no preconditions.
    let password = generate_setup_password(|| unsafe { esp_idf_svc::sys::esp_random() });
    wifi.stop().context("stopping the radio after the draw")?;
    Ok(password)
}

/// The AP configuration for the portal: WPA2-Personal with the per-run
/// password, default channel. No unsecured networks, ever.
fn access_point_configuration(ssid: &str, password: &str) -> Result<Configuration> {
    Ok(Configuration::AccessPoint(AccessPointConfiguration {
        ssid: ssid
            .try_into()
            .map_err(|_| anyhow::anyhow!("AP SSID too long"))?,
        auth_method: AuthMethod::WPA2Personal,
        password: password
            .try_into()
            .map_err(|_| anyhow::anyhow!("AP password too long"))?,
        ..Default::default()
    }))
}

/// Serves connections until one submission saves (a 200 from `/submit`).
/// Each connection gets its own short-lived thread: phones open several
/// sockets at once (captive-portal probes, speculative preconnects) and some
/// never send a byte, so a one-at-a-time loop would leave the real page
/// request queued behind a stalled socket until its timeout. A rejection
/// answers the browser and keeps the portal up for a retry; the e-paper
/// needs no update in between.
fn serve<S: DeviceConfigStore + Send>(listener: &TcpListener, store: &mut S) -> Result<()> {
    // Polled accept, so the loop can notice a save made by a handler thread.
    listener
        .set_nonblocking(true)
        .context("making the portal listener non-blocking")?;
    let store = Mutex::new(store);
    let saved = AtomicBool::new(false);
    let (store, saved) = (&store, &saved);
    thread::scope(|scope| {
        while !saved.load(Ordering::Acquire) {
            match listener.accept() {
                Ok((connection, _)) => {
                    thread::Builder::new()
                        .stack_size(HANDLER_STACK)
                        .spawn_scoped(scope, move || handle(connection, store, saved))
                        .context("spawning a portal connection handler")?;
                }
                Err(e) if e.kind() == ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(20));
                }
                Err(e) => return Err(e).context("accepting a portal connection"),
            }
        }
        Ok(())
    })
}

/// One connection: read the request, route it, answer, close.
fn handle<S: DeviceConfigStore>(
    mut connection: TcpStream,
    store: &Mutex<&mut S>,
    saved: &AtomicBool,
) {
    // Whether an accepted socket inherits the listener's mode varies by stack.
    let _ = connection.set_nonblocking(false);
    let _ = connection.set_read_timeout(Some(READ_TIMEOUT));
    let Some(request) = read_request(&mut connection) else {
        return;
    };
    let (status, body) = {
        let mut store = store.lock().unwrap_or_else(PoisonError::into_inner);
        route(&mut **store, request.method, &request.path, &request.body)
    };
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

    match (status, request.path.as_str()) {
        (200, "/submit") => saved.store(true, Ordering::Release),
        (400, _) => warn!("Submission rejected; the portal stays up."),
        (500, _) => warn!("Config store refused the save; the portal stays up."),
        _ => {}
    }
}

/// One parsed request: method, path, and (for POSTs) the body.
struct ParsedRequest {
    method: Method,
    path: String,
    body: Vec<u8>,
}

/// Reads the request head and its content-length-framed body, in chunks.
/// Malformed or oversized input yields `None`; the portal just moves on.
fn read_request(connection: &mut TcpStream) -> Option<ParsedRequest> {
    let mut buffer = Vec::new();
    let mut chunk = [0u8; 512];
    let head_end = loop {
        if let Some(at) = buffer.windows(4).position(|w| w == b"\r\n\r\n") {
            break at + 4;
        }
        if buffer.len() > REQUEST_MAX {
            return None;
        }
        match connection.read(&mut chunk) {
            Ok(0) | Err(_) => return None,
            Ok(n) => buffer.extend_from_slice(&chunk[..n]),
        }
    };

    // Whatever followed the head in the same reads is already body.
    let mut body = buffer.split_off(head_end);
    let head = String::from_utf8(buffer).ok()?;
    let request_line = head.lines().next()?;
    let mut parts = request_line.split_whitespace();
    let method = match parts.next()? {
        "POST" => Method::Post,
        // Anything else — including GET — reads the form.
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
        .min(REQUEST_MAX);

    while body.len() < content_length {
        match connection.read(&mut chunk) {
            Ok(0) | Err(_) => return None,
            Ok(n) => body.extend_from_slice(&chunk[..n]),
        }
    }
    body.truncate(content_length);

    Some(ParsedRequest { method, path, body })
}
