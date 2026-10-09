//! Comack firmware for the Seeed reTerminal E1001 (ESP32-S3).
//!
//! The composition root: builds the ESP-IDF and MicroLink adapters, plugs
//! them into the `comack-core` use cases and schedules them.
//!
//! * Wi-Fi supervisor thread: joins and rotates networks.
//! * `AudioStreamer` thread on core 0: microphone to the server's WebSocket.
//! * Main task (core 1): `ComicViewer` polling the server, driving the
//!   e-paper and the front buttons.

mod buttons;
mod clock;
mod latch;
mod led;
mod microphone;
mod nvs_config;
mod secrets;
mod tailnet;
mod wifi;

use std::ffi::{CStr, CString};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use comack_core::adapters::epd::CHUNK_BYTES;
use comack_core::adapters::{
    Epd7in5, HttpClient, HttpComicService, HttpConfig, PressLatch, ServerLocator, WebSocketUplink,
    WsConfig,
};
use comack_core::application::{
    AudioStreamer, ComicViewer, ConfigSource, StreamerConfig, ViewerConfig, load_with_source,
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
use crate::led::InvertedLed;
use crate::microphone::PdmMicrophone;
use crate::nvs_config::NvsDeviceConfigStore;
use crate::tailnet::{Tailnet, TailnetConfig};

fn main() -> Result<()> {
    esp_idf_svc::sys::link_patches();
    EspLogger::initialize_default();
    info!("COMACK: reTerminal E1001 display, native Tailscale node via MicroLink");

    let peripherals = Peripherals::take()?;
    let pins = peripherals.pins;
    let sysloop = EspSystemEventLoop::take()?;
    // Erases and re-initialises NVS if its layout changed.
    let nvs = EspDefaultNvsPartition::take()?;

    // The configuration the device runs with: what the on-chip store
    // holds, else this build's compile-time secrets.
    let config = effective_config(&nvs)?;

    // Large allocations land in PSRAM (CONFIG_SPIRAM_USE_MALLOC).
    let mut frame: Box<Frame> = vec![0xFF; FRAME_BYTES]
        .into_boxed_slice()
        .try_into()
        .map_err(|_| anyhow!("frame buffer size"))?;

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
    let display = Epd7in5::new(
        spi,
        PinDriver::output(pins.gpio11)?,          // DC
        PinDriver::output(pins.gpio12)?,          // RST
        PinDriver::input(pins.gpio13, Pull::Up)?, // BUSY, low while busy
        FreeRtos,
    );
    // Front buttons; falling-edge interrupts latch presses that arrive
    // while the viewer's loop is blocked (see InterruptButtons).
    let buttons = InterruptButtons::new(
        Arc::new(PressLatch::new()),
        PinDriver::input(pins.gpio4, Pull::Up)?, // right white: forward
        PinDriver::input(pins.gpio5, Pull::Up)?, // left white: back
        PinDriver::input(pins.gpio3, Pull::Up)?, // green: trigger
    )
    .context("buttons")?;
    // Onboard LED, inverted logic: LOW means on (Seeed board wiki).
    let led = InvertedLed::new(PinDriver::output(pins.gpio6)?);

    wifi::start(
        peripherals.modem,
        sysloop,
        nvs,
        config.networks.clone(),
    )?
    .wait();

    // MicroLink keeps the pointers it is given, so the configured identity
    // leaks once and lives for the power cycle — including the server host,
    // which the audio thread borrows beyond this stack frame.
    let device_name: &'static _ =
        Box::leak(CString::new(config.device_name.as_str())?.into_boxed_c_str());
    let auth_key: &'static CStr =
        Box::leak(CString::new(config.tailscale_auth_key.as_str())?.into_boxed_c_str());
    let host: &'static str =
        Box::leak(String::from(config.server_host.as_str()).into_boxed_str());
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

    spawn_audio_streamer(tailnet, microphone, host, config.server_port)?;

    tailnet.wait_registered();
    let locator = ServerLocator::new(tailnet, host);
    let server = loop {
        if let Some(ip) = locator.locate() {
            break ip;
        }
        warn!("Resolving '{host}' via Tailscale MagicDNS...");
        thread::sleep(Duration::from_secs(1));
    };
    info!("MagicDNS resolved '{host}' -> {server}");

    let comics = HttpComicService::new(
        HttpClient::new(tailnet, host, config.server_port, HttpConfig::default()),
        server,
    );
    let mut viewer = ComicViewer::new(
        comics,
        display,
        buttons,
        EspClock,
        comics, // the same HTTP service reports the presses
        led,
        &mut frame,
        ViewerConfig::default(),
    );
    loop {
        thread::sleep(viewer.tick());
    }
}

/// The configuration the device runs with: what the on-chip store holds,
/// else this build's compile-time `secrets.rs` and `config.rs` values.
///
/// A missing, unreadable or unopenable store degrades to the fallback with
/// a logged reason — never to a failed boot — so an already-flashed device
/// keeps coming up exactly as it did before runtime configuration existed.
fn effective_config(nvs: &EspDefaultNvsPartition) -> Result<DeviceConfig> {
    let fallback = DeviceConfig::from_compile_time(
        &secrets::wifi_networks(),
        config::SERVER,
        config::DEVICE_NAME,
        secrets::TAILSCALE_AUTH_KEY
            .to_str()
            .context("Tailscale auth key must be UTF-8")?,
    )
    .context("this build's compile-time secrets")?;

    let store = match NvsDeviceConfigStore::open(nvs.clone()) {
        Ok(store) => store,
        Err(e) => {
            warn!("Config store unavailable ({e:#}); using compile-time secrets.");
            return Ok(fallback);
        }
    };
    match load_with_source(&store, &fallback) {
        Ok((config, source)) => {
            info!(
                "Device configuration: {}.",
                match source {
                    ConfigSource::Stored => "stored on the device (NVS)",
                    ConfigSource::Fallback => "compile-time secrets (nothing stored)",
                }
            );
            Ok(config)
        }
        Err(e) => {
            warn!("Config store unreadable ({e:#}); using compile-time secrets.");
            Ok(fallback)
        }
    }
}

/// Stream audio from a thread pinned to core 0, as the C++ firmware did.
fn spawn_audio_streamer(
    tailnet: Tailnet,
    microphone: PdmMicrophone,
    host: &'static str,
    port: u16,
) -> Result<()> {
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
        tailnet.wait_registered();
        let locator = ServerLocator::new(tailnet, host);
        let uplink =
            WebSocketUplink::new(tailnet, locator, port, AUDIO_WS_PATH, WsConfig::default());
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
