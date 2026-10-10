//! Comack firmware for the Seeed reTerminal E1001 (ESP32-S3).
//!
//! The composition root: builds the ESP-IDF and MicroLink adapters, plugs
//! them into the `comack-core` use cases and schedules them.
//!
//! * Unconfigured boots (or a 3 s hold of the left/back button) run the
//!   provisioning portal: a `comack-setup` SoftAP with a phone-friendly
//!   setup form; the e-paper shows instructions and progress.
//! * Wi-Fi supervisor thread: joins and rotates networks.
//! * `AudioStreamer` thread on core 0: microphone to the server's WebSocket.
//! * Main task (core 1): `ComicViewer` polling the server, driving the
//!   e-paper and the front buttons.

mod buttons;
mod clock;
mod lan;
mod latch;
mod led;
mod microphone;
mod nvs_config;
mod portal;
mod screens;
mod secrets;
mod setup_button;
mod tailnet;
mod wifi;

use std::ffi::{CStr, CString};
use std::net::{Ipv4Addr, SocketAddrV4};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use comack_core::adapters::epd::CHUNK_BYTES;
use comack_core::adapters::{
    DeviceIdentity, Epd7in5, HostResolver, HttpClient, HttpComicService, HttpConfig, PressLatch,
    ServerLocator, TcpConnector, WebSocketUplink, WsConfig,
};
use comack_core::application::ports::{ButtonInput, FrameDisplay};
use comack_core::application::{
    load_with_source, AudioStreamer, ComicViewer, ConfigSource, StreamerConfig, ViewerConfig,
};
use comack_core::config::{self, AUDIO_CHUNK_SAMPLES, AUDIO_SAMPLE_RATE_HZ, AUDIO_WS_PATH};
use comack_core::domain::{DeviceConfig, Frame, FRAME_BYTES};
use esp_idf_svc::eventloop::EspSystemEventLoop;
use esp_idf_svc::hal::cpu::Core;
use esp_idf_svc::hal::delay::FreeRtos;
use esp_idf_svc::hal::gpio::{AnyIOPin, PinDriver, Pull};
use esp_idf_svc::hal::peripherals::Peripherals;
use esp_idf_svc::hal::spi::config::{Config as SpiConfig, DriverConfig, MODE_0};
use esp_idf_svc::hal::spi::{Dma, SpiDeviceDriver, SpiDriver};
use esp_idf_svc::hal::task::thread::ThreadSpawnConfiguration;
use esp_idf_svc::hal::units::FromValueType;
use esp_idf_svc::log::EspLogger;
use esp_idf_svc::nvs::EspDefaultNvsPartition;
use log::{info, warn};

use crate::buttons::InterruptButtons;
use crate::clock::EspClock;
use crate::lan::{LanConnector, LanResolver};
use crate::led::InvertedLed;
use crate::microphone::PdmMicrophone;
use crate::nvs_config::NvsDeviceConfigStore;
use crate::setup_button::SetupHold;
use crate::tailnet::{Tailnet, TailnetConfig};

/// The server probe timeout inside [`wait_for_server`]: a connect that
/// hangs this long counts as unreachable, not merely slow.
const PROBE_TIMEOUT: Duration = Duration::from_secs(2);

/// How long Tailscale registration may stay pending before the e-paper
/// names the likely culprit (the auth key). Registration keeps being
/// awaited — a slow coordinator eventually completes.
const REGISTRATION_TIMEOUT: Duration = Duration::from_secs(30);

fn main() -> Result<()> {
    esp_idf_svc::sys::link_patches();
    EspLogger::initialize_default();
    info!("COMACK: reTerminal E1001 display, native Tailscale node via MicroLink");

    let peripherals = Peripherals::take()?;
    let pins = peripherals.pins;
    let sysloop = EspSystemEventLoop::take()?;
    // Erases and re-initialises NVS if its layout changed.
    let nvs = EspDefaultNvsPartition::take()?;

    // The setup button first: a hold at boot re-enters provisioning before
    // anything else runs. The same pin later becomes the viewer's back
    // button, whose tick keeps feeding the shared hold detector.
    let mut setup_pin = PinDriver::input(pins.gpio5, Pull::Up)?;
    let forced_setup = setup_button::held_now(&mut setup_pin);
    let setup_hold = Arc::new(SetupHold::new());

    // The configuration the device runs with: what the on-chip store
    // holds, else this build's compile-time secrets.
    let mut store = match NvsDeviceConfigStore::open(nvs.clone()) {
        Ok(store) => Some(store),
        Err(e) => {
            warn!("Config store unavailable ({e:#}); compile-time secrets only.");
            None
        }
    };
    let (mut config, mut stored) = effective_config(store.as_ref())?;
    let unconfigured = config.networks.is_empty();

    // Large allocations land in PSRAM (CONFIG_SPIRAM_USE_MALLOC); the
    // portal's screens share this buffer before the viewer reuses it as
    // the download buffer.
    let mut frame: Box<Frame> = vec![0xFF; FRAME_BYTES]
        .into_boxed_slice()
        .try_into()
        .map_err(|_| anyhow::anyhow!("frame buffer size"))?;

    // Board wiring: see firmware/README.md.
    let microphone = PdmMicrophone::new(
        peripherals.i2s0,
        pins.gpio42, // PDM CLK
        pins.gpio41, // PDM DIN
        pins.gpio38, // mic power enable
        AUDIO_SAMPLE_RATE_HZ,
        AUDIO_CHUNK_SAMPLES,
    )
    .context("microphone")?;

    let spi_bus = SpiDriver::new(
        peripherals.spi2,
        pins.gpio7, // SCK
        pins.gpio9, // MOSI
        None::<AnyIOPin<'static>>,
        &DriverConfig::new().dma(Dma::Auto(CHUNK_BYTES + 32)),
    )?;
    let spi = SpiDeviceDriver::new(
        spi_bus,
        Some(pins.gpio10), // CS
        &SpiConfig::new()
            .baudrate(10.MHz().into())
            .data_mode(MODE_0)
            .queue_size(1),
    )?;
    let mut display = Epd7in5::new(
        spi,
        PinDriver::output(pins.gpio11)?,          // DC
        PinDriver::output(pins.gpio12)?,          // RST
        PinDriver::input(pins.gpio13, Pull::Up)?, // BUSY, low while busy
        FreeRtos,
    );
    // Front buttons; falling-edge interrupts latch presses that arrive
    // while the viewer's loop is blocked (see InterruptButtons).
    let mut buttons = InterruptButtons::new(
        Arc::new(PressLatch::new()),
        PinDriver::input(pins.gpio4, Pull::Up)?, // right white: forward
        setup_pin,                               // left white: back + setup hold
        PinDriver::input(pins.gpio3, Pull::Up)?, // green: trigger
        Arc::clone(&setup_hold),
    )
    .context("buttons")?;
    // Onboard LED, inverted logic: LOW means on (Seeed board wiki).
    let led = InvertedLed::new(PinDriver::output(pins.gpio6)?);

    // One Wi-Fi stack for the whole run: the portal may borrow it for AP
    // mode first, then the supervisor takes it for station mode.
    let mut wifi = wifi::stack(peripherals.modem, sysloop, nvs)?;

    if forced_setup || unconfigured {
        let Some(store) = store.as_mut() else {
            anyhow::bail!("provisioning needs the on-chip config store, which failed to open");
        };
        portal::run(&mut wifi, &mut display, &mut frame, store)?;
        (config, stored) = effective_config(Some(store))?;
    }

    // The connecting screen only for a provisioned config; a
    // compile-time-secrets boot stays silent, as it always has.
    if stored {
        if let Some(first) = config.networks.first() {
            screens::connecting(&mut display, &mut frame, first.ssid.as_str())
                .map_err(|e| anyhow::anyhow!("connecting screen: {e:?}"))?;
        }
    }

    wifi::supervise_networks(wifi, config.networks.clone())?.wait();

    // MicroLink keeps the pointers it is given, so the configured identity
    // leaks once and lives for the power cycle — including the server host,
    // which the audio thread borrows beyond this stack frame.
    let device_name: &'static _ =
        Box::leak(CString::new(config.device_name.as_str())?.into_boxed_c_str());
    let host: &'static str = Box::leak(String::from(config.server_host.as_str()).into_boxed_str());
    let auth_key: &'static CStr =
        Box::leak(CString::new(config.tailscale_auth_key.as_str())?.into_boxed_c_str());
    // The audio thread borrows the identity for 'static, so it is leaked
    // like the host above: the configured pair lives for the power cycle.
    let identity = DeviceIdentity {
        device_id: Box::leak(String::from(config.device_id.as_str()).into_boxed_str()),
        device_token: Box::leak(String::from(config.device_token.as_str()).into_boxed_str()),
    };

    if config.tailscale_auth_key.is_empty() {
        // No tailnet: an entirely legitimate setup. The server must be on
        // the same LAN and reachable by its DNS name or literal IP.
        info!("No Tailscale auth key configured: running LAN-only (no tailnet).");
        run_viewer::<LanConnector, LanResolver>(
            LanConnector,
            LanResolver,
            microphone,
            display,
            buttons,
            led,
            &mut frame,
            host,
            config.server_port,
            identity,
            &setup_hold,
        )?;
    } else {
        info!(
            "Starting Tailscale (auth key {:.15}...)",
            auth_key.to_string_lossy()
        );
        let tailnet = Tailnet::start(&TailnetConfig {
            auth_key,
            device_name,
            max_peers: 16,
            wifi_tx_power_dbm: 13,
        })?;
        wait_for_registration(
            &tailnet,
            &mut display,
            &mut frame,
            &mut buttons,
            &setup_hold,
        )?;
        run_viewer::<Tailnet, Tailnet>(
            tailnet,
            tailnet,
            microphone,
            display,
            buttons,
            led,
            &mut frame,
            host,
            config.server_port,
            identity,
            &setup_hold,
        )?;
    }
    unreachable!("run_viewer never returns")
}

/// The configuration the device runs with: what the on-chip store holds,
/// else this build's compile-time `secrets.rs` and `config.rs` values.
/// Returns whether the configuration came from the store — the switch
/// that decides whether boot shows the connecting screens.
///
/// A missing, unreadable or unopenable store degrades to the fallback with
/// a logged reason — never to a failed boot — so an already-flashed device
/// keeps coming up exactly as it did before runtime configuration existed.
fn effective_config(store: Option<&NvsDeviceConfigStore>) -> Result<(DeviceConfig, bool)> {
    let fallback = DeviceConfig::from_compile_time(
        &secrets::wifi_networks(),
        config::SERVER,
        config::DEVICE_NAME,
        secrets::DEVICE_ID,
        secrets::DEVICE_TOKEN,
        secrets::TAILSCALE_AUTH_KEY
            .to_str()
            .context("Tailscale auth key must be UTF-8")?,
    )
    .context("this build's compile-time secrets")?;

    let Some(store) = store else {
        return Ok((fallback, false));
    };
    match load_with_source(store, &fallback) {
        Ok((config, source)) => {
            info!(
                "Device configuration: {}.",
                match source {
                    ConfigSource::Stored => "stored on the device (NVS)",
                    ConfigSource::Fallback => "compile-time secrets (nothing stored)",
                }
            );
            Ok((config, source == ConfigSource::Stored))
        }
        Err(e) => {
            warn!("Config store unreadable ({e:#}); using compile-time secrets.");
            Ok((fallback, false))
        }
    }
}

/// Bounded wait for tailnet registration. Past [`REGISTRATION_TIMEOUT`]
/// the e-paper names the auth key as the likely culprit; the wait itself
/// continues, and the setup button stays live throughout.
fn wait_for_registration(
    tailnet: &Tailnet,
    display: &mut impl FrameDisplay,
    frame: &mut Frame,
    buttons: &mut InterruptButtons,
    setup_hold: &SetupHold,
) -> Result<()> {
    let start = Instant::now();
    let mut stuck_shown = false;
    while !tailnet.registered().is_set() {
        // Feeds the setup-hold detector via the back pin.
        let _ = buttons.levels();
        if setup_hold.tripped() {
            restart_into_setup();
        }
        if !stuck_shown && start.elapsed() >= REGISTRATION_TIMEOUT {
            screens::tailscale_stuck(display, frame)
                .map_err(|e| anyhow::anyhow!("tailscale screen: {e:?}"))?;
            stuck_shown = true;
        }
        thread::sleep(Duration::from_secs(1));
    }
    Ok(())
}

/// Resolve the configured server and prove it accepts connections, with
/// the unreachable screen naming the retry loop. The setup button stays
/// live: a hold restarts the device into provisioning.
fn wait_for_server<C, R>(
    locator: &ServerLocator<'_, R>,
    connector: C,
    display: &mut impl FrameDisplay,
    frame: &mut Frame,
    host: &str,
    port: u16,
    buttons: &mut InterruptButtons,
    setup_hold: &SetupHold,
) -> Result<Ipv4Addr>
where
    C: TcpConnector,
    R: HostResolver,
{
    let mut screen_shown = false;
    loop {
        // Feeds the setup-hold detector via the back pin.
        let _ = buttons.levels();
        if setup_hold.tripped() {
            restart_into_setup();
        }
        if let Some(ip) = locator.locate() {
            if connector
                .connect(SocketAddrV4::new(ip, port), PROBE_TIMEOUT)
                .is_ok()
            {
                return Ok(ip);
            }
            info!("Server {host}:{port} refused the probe at {ip}; retrying.");
        } else {
            warn!("Resolving '{host}' (MagicDNS or DNS)...");
        }
        if !screen_shown {
            screens::server_unreachable(display, frame, host, port)
                .map_err(|e| anyhow::anyhow!("unreachable screen: {e:?}"))?;
            screen_shown = true;
        }
        thread::sleep(Duration::from_secs(1));
    }
}

/// Everything after Wi-Fi and (when configured) the tailnet: probe the
/// server, spawn the audio streamer, and run the viewer loop until the
/// setup button sends the device back into provisioning. Returns only on
/// a hard error in the pre-viewer steps; the viewer loop never ends.
#[allow(clippy::too_many_arguments)] // one argument per wired port
fn run_viewer<C, R>(
    connector: C,
    resolver: R,
    microphone: PdmMicrophone,
    mut display: impl FrameDisplay,
    mut buttons: InterruptButtons,
    led: InvertedLed,
    frame: &mut Frame,
    host: &'static str,
    port: u16,
    identity: DeviceIdentity<'static>,
    setup_hold: &SetupHold,
) -> Result<()>
where
    C: TcpConnector + Copy + Send + 'static,
    R: HostResolver + Copy + Send + 'static,
{
    let locator = ServerLocator::new(resolver, host);
    let server = wait_for_server(
        &locator,
        connector,
        &mut display,
        frame,
        host,
        port,
        &mut buttons,
        setup_hold,
    )?;
    info!("Server '{host}' reachable at {server}");

    spawn_audio_streamer(connector, resolver, microphone, host, port, identity)?;

    let client = HttpClient::new(connector, host, port, HttpConfig::default());
    let comics = HttpComicService::new(
        client,
        server,
        // The configured identity (stored config, else compile-time) rides
        // on the comic poll, the trigger, and the audio WebSocket handshake;
        // empty keeps the device on the server's `default` device as before.
        identity,
    );
    let mut viewer = ComicViewer::new(
        comics,
        display,
        buttons,
        EspClock,
        comics, // the same HTTP service reports the presses
        led,
        frame,
        ViewerConfig::default(),
    );
    loop {
        thread::sleep(viewer.tick());
        if setup_hold.tripped() {
            restart_into_setup();
        }
    }
}

fn restart_into_setup() -> ! {
    info!("Setup button held; restarting into provisioning.");
    // SAFETY: a clean restart into provisioning — no references outlive it.
    unsafe { esp_idf_svc::sys::esp_restart() }
}

/// Stream audio from a thread pinned to core 0, as the C++ firmware did.
/// Works for either transport: MicroLink tailnet sockets or plain LAN.
fn spawn_audio_streamer<C, R>(
    connector: C,
    resolver: R,
    microphone: PdmMicrophone,
    host: &'static str,
    port: u16,
    identity: DeviceIdentity<'static>,
) -> Result<()>
where
    C: TcpConnector + Send + Copy + 'static,
    R: HostResolver + Send + Copy + 'static,
{
    const STACK: usize = 8192;
    ThreadSpawnConfiguration {
        name: Some(c"AudioStreamer"),
        stack_size: STACK,
        priority: 2,
        pin_to_core: Some(Core::Core0),
        ..Default::default()
    }
    .set()?;
    let spawned = thread::Builder::new().stack_size(STACK).spawn(move || {
        let locator = ServerLocator::new(resolver, host);
        let uplink = WebSocketUplink::new(
            connector,
            locator,
            port,
            AUDIO_WS_PATH,
            identity,
            WsConfig::default(),
        );
        let mut buffer = vec![0i16; AUDIO_CHUNK_SAMPLES];
        let mut streamer =
            AudioStreamer::new(microphone, uplink, &mut buffer, StreamerConfig::default());
        info!("Audio streaming task running on core 0.");
        loop {
            thread::sleep(streamer.tick());
        }
    });
    ThreadSpawnConfiguration::default().set()?;
    spawned?;
    Ok(())
}
