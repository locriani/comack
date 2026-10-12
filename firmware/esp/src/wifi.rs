//! Wi-Fi station with rotation through the configured networks, plus the
//! access-point mode the provisioning portal will build on.

use std::sync::Arc;
use std::thread;
use std::time::Duration;

use anyhow::{anyhow, Result};
use comack_core::domain::{NetworkRotation, WifiCredentials, MAX_NETWORKS};
use esp_idf_svc::eventloop::EspSystemEventLoop;
use esp_idf_svc::hal::modem::WifiModemPeripheral;
use esp_idf_svc::nvs::EspDefaultNvsPartition;
use esp_idf_svc::sys::{esp, esp_wifi_set_ps, wifi_ps_type_t_WIFI_PS_NONE};
use esp_idf_svc::wifi::{
    AccessPointInfo, AuthMethod, BlockingWifi, ClientConfiguration, Configuration, EspWifi,
};
use log::{info, warn};

use crate::latch::Latch;

const CHECK_INTERVAL: Duration = Duration::from_secs(1);

/// Auth modes to try for a password-protected AP. Modern dual-band APs are
/// often WPA3 or WPA2/WPA3; older ones are WPA2. We try each before rotating
/// to the next configured network.
const PASSWORD_AUTH_METHODS: &[AuthMethod] = &[
    AuthMethod::WPA2WPA3Personal,
    AuthMethod::WPA3Personal,
    AuthMethod::WPA2Personal,
    AuthMethod::WPAWPA2Personal,
];

/// Constructs the Wi-Fi stack without starting it. The plain station
/// boot and the provisioning portal share one construction; whoever runs
/// first owns the modem.
pub fn stack(
    modem: impl WifiModemPeripheral + 'static,
    sysloop: EspSystemEventLoop,
    nvs: EspDefaultNvsPartition,
) -> Result<BlockingWifi<EspWifi<'static>>> {
    Ok(BlockingWifi::wrap(
        EspWifi::new(modem, sysloop.clone(), Some(nvs))?,
        sysloop,
    )?)
}

/// Start Wi-Fi and a supervisor thread that keeps it connected, moving to
/// the next network whenever the current one drops or cannot be joined.
/// The returned latch is set once the first connection has an IP address.
///
/// The networks come from the device configuration — the NVS store when
/// something is provisioned, otherwise this build's `secrets.rs` values.
/// Configures a (already-constructed, stopped) stack for station mode on
/// `networks` and starts the supervisor. The provisioning path calls this
/// after the portal's access point has shut down; main calls it directly
/// on a normal boot.
pub fn supervise_networks(
    mut wifi: BlockingWifi<EspWifi<'static>>,
    networks: heapless::Vec<WifiCredentials, MAX_NETWORKS>,
) -> Result<Arc<Latch>> {
    let Some(rotation) = NetworkRotation::new(networks) else {
        anyhow::bail!(
            "no Wi-Fi networks configured (device config store and secrets.rs both empty)"
        );
    };
    wifi.set_configuration(&client_config(rotation.current(), AuthMethod::WPA2WPA3Personal, None)?)?;
    wifi.start()?;
    // Power save off: WireGuard wants low latency.
    // SAFETY: Wi-Fi is initialised and started.
    esp!(unsafe { esp_wifi_set_ps(wifi_ps_type_t_WIFI_PS_NONE) })?;

    let online = Arc::new(Latch::new());
    let signal = online.clone();
    thread::Builder::new()
        .name("wifi".into())
        .stack_size(8192)
        .spawn(move || supervise(wifi, rotation, &signal))?;
    Ok(online)
}

/// Optional 2.4 GHz BSSID + channel from a scan (defeats band-steering that
/// would otherwise hand the ESP32 a 5 GHz AP it cannot join).
type ApTarget = ([u8; 6], u8);

fn client_config(
    network: &WifiCredentials,
    auth_method: AuthMethod,
    target: Option<ApTarget>,
) -> Result<Configuration> {
    let (bssid, channel) = match target {
        Some((bssid, channel)) => (Some(bssid), Some(channel)),
        None => (None, None),
    };
    Ok(Configuration::Client(ClientConfiguration {
        ssid: network
            .ssid
            .as_str()
            .try_into()
            .map_err(|_| anyhow!("SSID too long"))?,
        password: network
            .password
            .as_str()
            .try_into()
            .map_err(|_| anyhow!("password too long"))?,
        auth_method,
        bssid,
        channel,
        ..Default::default()
    }))
}

fn supervise(
    mut wifi: BlockingWifi<EspWifi<'static>>,
    mut rotation: NetworkRotation,
    online: &Latch,
) {
    let mut was_connected = false;
    loop {
        if !wifi.is_connected().unwrap_or(false) {
            if was_connected {
                warn!("Wi-Fi disconnected; rotating to the next network.");
                rotation.advance();
                was_connected = false;
            }
            let network = rotation.current();
            info!("Connecting to Wi-Fi '{}'...", network.ssid.as_str());
            match join(&mut wifi, network) {
                Ok(()) => {
                    let ip = wifi.wifi().sta_netif().get_ip_info().map(|i| i.ip);
                    info!(
                        "Wi-Fi connected to '{}'; local IP {ip:?}",
                        network.ssid.as_str()
                    );
                    was_connected = true;
                    online.set();
                }
                Err(e) => {
                    warn!("Could not join '{}': {e}", network.ssid.as_str());
                    rotation.advance();
                }
            }
        }
        thread::sleep(CHECK_INTERVAL);
    }
}

fn join(wifi: &mut BlockingWifi<EspWifi<'static>>, network: &WifiCredentials) -> Result<()> {
    let target = pick_2ghz_ap(wifi, network.ssid.as_str());
    let methods: &[AuthMethod] = if network.password.is_empty() {
        &[AuthMethod::None]
    } else {
        PASSWORD_AUTH_METHODS
    };

    let mut last = anyhow!("no auth method attempted");
    for &auth in methods {
        info!(
            "Trying '{}' with {:?}{}",
            network.ssid.as_str(),
            auth,
            match target {
                Some((_, ch)) => format!(" on 2.4 GHz ch {ch}"),
                None => String::new(),
            }
        );
        match try_join(wifi, network, auth, target) {
            Ok(()) => return Ok(()),
            Err(e) => {
                warn!("  {:?}: {e}", auth);
                last = e;
            }
        }
    }
    Err(last)
}

fn try_join(
    wifi: &mut BlockingWifi<EspWifi<'static>>,
    network: &WifiCredentials,
    auth: AuthMethod,
    target: Option<ApTarget>,
) -> Result<()> {
    let _ = wifi.disconnect();
    wifi.set_configuration(&client_config(network, auth, target)?)?;
    wifi.connect()?;
    wifi.wait_netif_up()?;
    Ok(())
}

/// Prefer a 2.4 GHz BSSID for `ssid` (channel 1–14). Band-steering APs often
/// advertise the same name on 5 GHz; the ESP32-S3 cannot join that radio.
fn pick_2ghz_ap(
    wifi: &mut BlockingWifi<EspWifi<'static>>,
    ssid: &str,
) -> Option<ApTarget> {
    let aps = match wifi.scan() {
        Ok(aps) => aps,
        Err(e) => {
            warn!("Wi-Fi scan failed ({e}); connecting without a pinned BSSID");
            return None;
        }
    };

    let mut best_24: Option<&AccessPointInfo> = None;
    let mut saw_ssid = false;
    for ap in &aps {
        if ap.ssid.as_str() != ssid {
            continue;
        }
        saw_ssid = true;
        if !(1..=14).contains(&ap.channel) {
            continue;
        }
        best_24 = match best_24 {
            Some(cur) if cur.signal_strength >= ap.signal_strength => Some(cur),
            _ => Some(ap),
        };
    }

    if let Some(ap) = best_24 {
        info!(
            "Scan: '{}' 2.4 GHz ch {} rssi {} auth {:?}",
            ssid, ap.channel, ap.signal_strength, ap.auth_method
        );
        return Some((ap.bssid, ap.channel));
    }

    if saw_ssid {
        warn!(
            "Scan: '{}' visible only on 5 GHz — ESP32 needs a 2.4 GHz BSSID",
            ssid
        );
    } else {
        warn!("Scan: '{}' not seen (out of range, hidden, or wrong name)", ssid);
    }
    None
}
