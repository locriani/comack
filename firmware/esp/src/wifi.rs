//! Wi-Fi station with rotation through the configured networks, plus the
//! access-point mode the provisioning portal will build on.

use std::sync::Arc;
use std::thread;
use std::time::Duration;

use anyhow::Result;
use comack_core::domain::{NetworkRotation, WifiCredentials, MAX_NETWORKS};
use esp_idf_svc::eventloop::EspSystemEventLoop;
use esp_idf_svc::hal::modem::WifiModemPeripheral;
use esp_idf_svc::nvs::EspDefaultNvsPartition;
use esp_idf_svc::sys::{esp, esp_wifi_set_ps, wifi_ps_type_t_WIFI_PS_NONE};
use esp_idf_svc::wifi::{
    AccessPointConfiguration, AuthMethod, BlockingWifi, ClientConfiguration, Configuration,
    EspWifi,
};
use log::{info, warn};

use crate::latch::Latch;

const CHECK_INTERVAL: Duration = Duration::from_secs(1);

/// Start Wi-Fi and a supervisor thread that keeps it connected, moving to
/// the next network whenever the current one drops or cannot be joined.
/// The returned latch is set once the first connection has an IP address.
///
/// The networks come from the device configuration — the NVS store when
/// something is provisioned, otherwise this build's `secrets.rs` values.
pub fn start(
    modem: impl WifiModemPeripheral + 'static,
    sysloop: EspSystemEventLoop,
    nvs: EspDefaultNvsPartition,
    networks: heapless::Vec<WifiCredentials, MAX_NETWORKS>,
) -> Result<Arc<Latch>> {
    let Some(rotation) = NetworkRotation::new(networks) else {
        anyhow::bail!("no Wi-Fi networks configured (device config store and secrets.rs both empty)");
    };
    let mut wifi = BlockingWifi::wrap(EspWifi::new(modem, sysloop.clone(), Some(nvs))?, sysloop)?;
    wifi.set_configuration(&client_config(rotation.current())?)?;
    wifi.start()?;
    // Power save off: WireGuard wants low latency.
    // SAFETY: Wi-Fi is initialised and started.
    esp!(unsafe { esp_wifi_set_ps(wifi_ps_type_t_WIFI_PS_NONE) })?;

    let online = Arc::new(Latch::new());
    let signal = online.clone();
    thread::Builder::new()
        .name("wifi".into())
        .stack_size(6144)
        .spawn(move || supervise(wifi, rotation, &signal))?;
    Ok(online)
}

fn client_config(network: &WifiCredentials) -> Result<Configuration> {
    Ok(Configuration::Client(ClientConfiguration {
        ssid: network
            .ssid
            .as_str()
            .try_into()
            .map_err(|_| anyhow::anyhow!("SSID too long"))?,
        password: network
            .password
            .as_str()
            .try_into()
            .map_err(|_| anyhow::anyhow!("password too long"))?,
        auth_method: if network.password.is_empty() {
            AuthMethod::None
        } else {
            AuthMethod::WPA2Personal
        },
        ..Default::default()
    }))
}

/// Builds the access-point configuration the provisioning portal will use.
///
/// Capability proof for the portal task: the AP mode API type-checks and
/// links on this stack (the esp32s3 cross-compile builds this function).
/// Exercising it on hardware is the portal PR's checklist. Unused until
/// the portal calls it.
#[allow(dead_code)]
pub fn access_point_configuration(ssid: &str) -> Result<Configuration> {
    Ok(Configuration::AccessPoint(AccessPointConfiguration {
        ssid: ssid
            .try_into()
            .map_err(|_| anyhow::anyhow!("SSID too long"))?,
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
    let _ = wifi.disconnect();
    wifi.set_configuration(&client_config(network)?)?;
    wifi.connect()?;
    wifi.wait_netif_up()?;
    Ok(())
}
