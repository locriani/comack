//! The on-device provisioning portal: a `comack-setup` access point with
//! the setup form served over plain HTTP.
//!
//! The pages, form decoding, validation and the store write all come from
//! the core's [`route`] — the same decision the simulator's loopback
//! server runs — so this file is only the transport: AP mode, one
//! connection at a time, request framing, and the screens between them.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::thread;
use std::time::Duration;

use anyhow::{Context, Result};
use comack_core::adapters::http::Method;
use comack_core::adapters::portal::route;
use comack_core::application::ports::{DeviceConfigStore, FrameDisplay};
use esp_idf_svc::wifi::{AccessPointConfiguration, BlockingWifi, Configuration, EspWifi};
use log::{info, warn};

use crate::screens;

/// The network the portal broadcasts; the instructions screen tells
/// people to join it.
pub const AP_SSID: &str = "comack-setup";

/// Where the form lives once joined. lwIP gives a SoftAP 192.168.4.1.
pub const PORTAL_URL: &str = "http://192.168.4.1";

/// The port the portal serves on: 80, so the URL needs no explicit port
/// on a phone.
const PORT: u16 = 80;

/// Per-connection read timeout: a phone that opens a socket and stalls
/// must not wedge the portal.
const READ_TIMEOUT: Duration = Duration::from_secs(10);

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
    S: DeviceConfigStore,
    D: FrameDisplay,
{
    screens::instructions(display, frame)
        .map_err(|e| anyhow::anyhow!("instructions screen: {e:?}"))?;

    wifi.set_configuration(&access_point_configuration(AP_SSID)?)
        .context("switching to access-point mode")?;
    wifi.start().context("starting the access point")?;
    // The driver's start() only requests startup; poll until the AP
    // interface is actually up so the HTTP listener has an IP to bind.
    while !matches!(wifi.is_up(), Ok(true)) {
        thread::sleep(Duration::from_millis(50));
    }

    let listener = TcpListener::bind(("0.0.0.0", PORT)).context("binding the portal port")?;
    info!("Portal AP '{AP_SSID}' up; open {PORTAL_URL}");

    serve(&listener, store)?;
    screens::saved(display, frame).map_err(|e| anyhow::anyhow!("saved screen: {e:?}"))?;

    // Hand the radio back: the supervisor reconfigures it for station mode.
    wifi.stop().context("stopping the access point")?;
    info!("Configuration saved; portal closed.");
    Ok(())
}

/// The AP configuration for the portal: open network, default channel.
/// The form is the only thing it serves, and only until a config is
/// saved — the single-user-appliance posture the spec accepts (device
/// paths stay tokenless; nothing stored is echoed back).
fn access_point_configuration(ssid: &str) -> Result<Configuration> {
    Ok(Configuration::AccessPoint(AccessPointConfiguration {
        ssid: ssid
            .try_into()
            .map_err(|_| anyhow::anyhow!("AP SSID too long"))?,
        ..Default::default()
    }))
}

/// Serves connections until one submission saves (a 200 from `/submit`).
/// Sequential on purpose: the portal serves one phone, not a fleet. A
/// rejection answers the browser and keeps the portal up for a retry; the
/// e-paper needs no update in between.
fn serve<S: DeviceConfigStore>(listener: &TcpListener, store: &mut S) -> Result<()> {
    for connection in listener.incoming() {
        let mut connection = connection.context("accepting a portal connection")?;
        let _ = connection.set_read_timeout(Some(READ_TIMEOUT));
        let Some(request) = read_request(&mut connection) else {
            continue;
        };
        let (status, body) = route(store, request.method, &request.path, &request.body);
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
            (200, "/submit") => return Ok(()),
            (400, _) => warn!("Submission rejected; the portal stays up."),
            (500, _) => warn!("Config store refused the save; the portal stays up."),
            _ => {}
        }
    }
    Ok(())
}

/// One parsed request: method, path, and (for POSTs) the body.
struct ParsedRequest {
    method: Method,
    path: String,
    body: Vec<u8>,
}

/// Reads the request head and its content-length-framed body. Malformed
/// or oversized input yields `None`; the portal just moves on.
fn read_request(connection: &mut TcpStream) -> Option<ParsedRequest> {
    let mut head = Vec::new();
    let mut byte = [0u8; 1];
    loop {
        match connection.read(&mut byte) {
            Ok(0) | Err(_) => return None,
            Ok(_) => {
                head.push(byte[0]);
                if head.ends_with(b"\r\n\r\n") {
                    break;
                }
                if head.len() > REQUEST_MAX {
                    return None;
                }
            }
        }
    }

    let head = String::from_utf8(head).ok()?;
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

    let mut body = vec![0u8; content_length];
    connection.read_exact(&mut body).ok()?;

    Some(ParsedRequest { method, path, body })
}
