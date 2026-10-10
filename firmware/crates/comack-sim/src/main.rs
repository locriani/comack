//! Run the Comack display firmware on the desktop.
//!
//! ```text
//! cargo run -p comack-sim -- [--host localhost] [--port 8000] [--out screen.pbm] [--no-audio]
//! ```
//!
//! Streams synthetic audio to the server, polls for comics and writes the
//! screen to a PBM file. Type `f`, `b` or `t` + Enter for the front buttons.

use std::process::ExitCode;
use std::thread;
use std::time::Duration;

use comack_core::adapters::{
    DeviceIdentity, HttpClient, HttpComicService, HttpConfig, ServerLocator, WebSocketUplink,
    WsConfig,
};
use comack_core::application::{AudioStreamer, ComicViewer, StreamerConfig, ViewerConfig};
use comack_core::config::{self, AUDIO_CHUNK_SAMPLES, AUDIO_SAMPLE_RATE_HZ, AUDIO_WS_PATH};
use comack_core::domain::FRAME_BYTES;
use comack_sim::{
    LogLed, PbmDisplay, StdClock, StdConnector, StdinButtons, SyntheticMicrophone, SystemResolver,
};
use log::{info, LevelFilter, Log, Metadata, Record};

struct StderrLogger;

impl Log for StderrLogger {
    fn enabled(&self, _: &Metadata<'_>) -> bool {
        true
    }

    fn log(&self, record: &Record<'_>) {
        eprintln!(
            "{:<5} {}: {}",
            record.level(),
            record.target(),
            record.args()
        );
    }

    fn flush(&self) {}
}

struct Options {
    host: String,
    port: u16,
    out: String,
    audio: bool,
}

fn parse_args() -> Result<Options, String> {
    let mut options = Options {
        host: config::SERVER.host.to_owned(),
        port: config::SERVER.port,
        out: "comack-screen.pbm".to_owned(),
        audio: true,
    };
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        let mut value = || args.next().ok_or(format!("{arg} needs a value"));
        match arg.as_str() {
            "--host" => options.host = value()?,
            "--port" => options.port = value()?.parse().map_err(|e| format!("--port: {e}"))?,
            "--out" => options.out = value()?,
            "--no-audio" => options.audio = false,
            "-h" | "--help" => return Err(String::new()),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    Ok(options)
}

fn main() -> ExitCode {
    let options = match parse_args() {
        Ok(o) => o,
        Err(e) => {
            if !e.is_empty() {
                eprintln!("error: {e}");
            }
            eprintln!(
                "usage: comack-sim [--host HOST] [--port PORT] [--out FILE.pbm] [--no-audio]"
            );
            return ExitCode::from(2);
        }
    };
    log::set_logger(&StderrLogger).expect("logger set once");
    log::set_max_level(LevelFilter::Info);

    // Leaked so both loops can borrow it for the life of the program.
    let host: &'static str = Box::leak(options.host.into_boxed_str());
    let port = options.port;

    if options.audio {
        thread::spawn(move || {
            let microphone = SyntheticMicrophone::new(
                AUDIO_SAMPLE_RATE_HZ,
                Duration::from_secs(5),
                Duration::from_secs(25),
                true,
            );
            let locator = ServerLocator::new(SystemResolver, host);
            let uplink = WebSocketUplink::new(
                StdConnector,
                locator,
                port,
                AUDIO_WS_PATH,
                DeviceIdentity::NONE,
                WsConfig::default(),
            );
            let mut buffer = vec![0i16; AUDIO_CHUNK_SAMPLES];
            let mut streamer =
                AudioStreamer::new(microphone, uplink, &mut buffer, StreamerConfig::default());
            loop {
                thread::sleep(streamer.tick());
            }
        });
    }

    let locator = ServerLocator::new(SystemResolver, host);
    let server = loop {
        if let Some(ip) = locator.locate() {
            break ip;
        }
        log::warn!("Resolving '{host}'...");
        thread::sleep(Duration::from_secs(1));
    };
    info!("'{host}' -> {server}; screen at {}", options.out);
    eprintln!("commands: f (forward), b (back), t (trigger)");

    let comics = HttpComicService::new(
        HttpClient::new(StdConnector, host, port, HttpConfig::default()),
        server,
        // No identity flags on the desktop sim: it presents none and the
        // server keeps it on the `default` device.
        DeviceIdentity::NONE,
    );
    let mut frame: Box<[u8; FRAME_BYTES]> = vec![0xFF; FRAME_BYTES]
        .into_boxed_slice()
        .try_into()
        .expect("frame size");
    let mut viewer = ComicViewer::new(
        comics,
        PbmDisplay::new(options.out),
        StdinButtons::spawn(),
        StdClock::new(),
        comics, // presses are reported to the server like on the device
        LogLed,
        &mut frame,
        ViewerConfig::default(),
    );
    loop {
        thread::sleep(viewer.tick());
    }
}
