//! The provisioning portal end to end: an unconfigured core serves the
//! setup form, a valid submission saves the configuration, and the saved
//! address is the one the network adapters dial — over real loopback
//! TCP, through the same `route` the device's SoftAP server runs.

#![allow(clippy::large_stack_arrays)]

mod support;

use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use comack_core::adapters::{
    DeviceIdentity, HttpClient, HttpComicService, HttpConfig, ServerLocator,
};
use comack_core::application::ports::{ComicSource, DeviceConfigStore};
use comack_core::domain::{Issue, SSID_MAX_LEN};
use comack_sim::{ProvisioningServer, StdConnector, SystemResolver};
use support::{frame_for, FakeServer, InMemoryConfigStore};

const WAIT: Duration = Duration::from_secs(2);

fn http(port: u16, request: &str) -> (u16, String) {
    let mut connection = TcpStream::connect(("127.0.0.1", port)).expect("connect");
    connection
        .set_read_timeout(Some(WAIT))
        .expect("read timeout");
    connection.write_all(request.as_bytes()).expect("send");
    let mut response = String::new();
    connection
        .read_to_string(&mut response)
        .expect("read the response");
    let status: u16 = response
        .split_whitespace()
        .nth(1)
        .and_then(|code| code.parse().ok())
        .expect("a status line");
    (status, response)
}

fn submit(port: u16, body: &str) -> (u16, String) {
    let request = format!(
        "POST /submit HTTP/1.1\r\nhost: 192.168.4.1\r\ncontent-type: application/x-www-form-urlencoded\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
        body.len()
    );
    http(port, &request)
}

#[test]
fn an_unconfigured_core_serves_the_form() {
    let store = Arc::new(Mutex::new(InMemoryConfigStore::default()));
    let portal = ProvisioningServer::start(store);
    let (status, response) = http(
        portal.port,
        "GET / HTTP/1.1\r\nhost: 192.168.4.1\r\nconnection: close\r\n\r\n",
    );
    assert_eq!(status, 200);
    // The form collects every field the spec names, and posts to the
    // device's own submit route.
    assert!(response.contains("name=\"ssid\""));
    assert!(response.contains("name=\"password\""));
    assert!(response.contains("name=\"server\""));
    assert!(response.contains("name=\"device_name\""));
    assert!(response.contains("name=\"tailscale_key\""));
    assert!(response.contains("action=\"/submit\""));
}

#[test]
fn a_valid_submission_saves_and_the_connector_dials_the_configured_host() {
    let server = FakeServer::start(|s| {
        s.issues.insert(7, frame_for(7));
        s.current = Some(7);
    });
    let store = Arc::new(Mutex::new(InMemoryConfigStore::default()));
    let portal = ProvisioningServer::start(store.clone());

    let (status, response) = submit(
        portal.port,
        &format!(
            "ssid=home&password=hunter2&server=127.0.0.1%3A{}&device_name=kitchen",
            server.port
        ),
    );
    assert_eq!(status, 200, "{response}");

    // The config landed in the store, exactly as submitted.
    let config = store
        .lock()
        .unwrap()
        .load()
        .expect("store readable")
        .expect("config saved");
    assert_eq!(config.networks.len(), 1);
    assert_eq!(config.networks[0].ssid.as_str(), "home");
    assert_eq!(config.networks[0].password.as_str(), "hunter2");
    assert_eq!(config.server_host.as_str(), "127.0.0.1");
    assert_eq!(config.server_port, server.port);
    assert_eq!(config.device_name.as_str(), "kitchen");

    // The saved address is the one the comic service dials: point the
    // service at the configured host/port the way the composition root
    // does after provisioning.
    let locator = ServerLocator::new(SystemResolver, "127.0.0.1");
    let ip = locator.locate().expect("the literal IP resolves");
    let http_config = HttpConfig {
        connect_timeout: WAIT,
        recv_timeout: WAIT,
    };
    let mut comics = HttpComicService::new(
        HttpClient::new(StdConnector, "127.0.0.1", server.port, http_config),
        ip,
        // Provisioning issues no identity, so the poll is the tokenless
        // bare path the server binds to its `default` device.
        DeviceIdentity::NONE,
    );
    assert_eq!(comics.latest_issue(), Ok(Issue::new(7)));
    assert!(server
        .state()
        .requests
        .iter()
        .any(|request| request.starts_with("GET /api/comics/current")));
}

#[test]
fn invalid_submissions_are_rejected_before_any_save() {
    let store = Arc::new(Mutex::new(InMemoryConfigStore::default()));
    let portal = ProvisioningServer::start(store.clone());

    for body in [
        "server=yourserver".to_string(),                 // no SSID
        "ssid=home".to_string(),                         // no server address
        "ssid=home&server=yourserver%3Aabc".to_string(), // port not a number
        format!("ssid={}&server=yourserver", "x".repeat(SSID_MAX_LEN + 1)), // SSID too long
    ] {
        let (status, response) = submit(portal.port, &body);
        assert_eq!(status, 400, "body {body:?} should be rejected: {response}");
        // Never strand the config: the store is untouched.
        assert_eq!(
            store.lock().unwrap().load().unwrap(),
            None,
            "a rejected submission must not save"
        );
    }
}

#[test]
fn a_new_submission_replaces_the_stored_config() {
    let store = Arc::new(Mutex::new(InMemoryConfigStore::default()));
    let portal = ProvisioningServer::start(store.clone());

    let (status, _) = submit(portal.port, "ssid=old-network&server=yourserver");
    assert_eq!(status, 200);
    let (status, _) = submit(portal.port, "ssid=new-network&server=yourserver%3A9000");
    assert_eq!(status, 200);

    let config = store.lock().unwrap().load().unwrap().expect("config saved");
    assert_eq!(config.networks.len(), 1);
    assert_eq!(config.networks[0].ssid.as_str(), "new-network");
    assert_eq!(config.server_port, 9000);
}
