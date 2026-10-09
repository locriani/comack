//! The real use cases and protocol adapters, over real sockets, against a
//! fake Comack server.

#![allow(clippy::large_stack_arrays)]

mod support;

use std::cell::{Cell, RefCell};
use std::convert::Infallible;
use std::net::Ipv4Addr;
use std::rc::Rc;
use std::time::Duration;

use comack_core::adapters::{
    ComicApiError, HostResolver, HttpClient, HttpComicService, HttpConfig, HttpError,
    ServerLocator, WebSocketUplink, WsConfig,
};
use comack_core::application::ports::{
    AudioSource, Clock, ComicSource, ComicTrigger, FrameDisplay,
};
use comack_core::application::{AudioStreamer, ComicViewer, StreamerConfig, ViewerConfig};
use comack_core::domain::{ButtonLevels, Frame, Instant, Issue, FRAME_BYTES};
use comack_sim::{ScriptedButtons, StdConnector, SyntheticMicrophone};
use support::{frame_for, FakeServer};

const WAIT: Duration = Duration::from_secs(5);

fn service(server: &FakeServer) -> HttpComicService<'static, StdConnector> {
    let config = HttpConfig {
        connect_timeout: Duration::from_secs(2),
        recv_timeout: Duration::from_secs(2),
    };
    HttpComicService::new(
        HttpClient::new(StdConnector, "comack-server", server.port, config),
        server.ip(),
    )
}

fn published(issues: &[u32], current: Option<u32>) -> FakeServer {
    FakeServer::start(|s| {
        for &n in issues {
            s.issues.insert(n, frame_for(n));
        }
        s.current = current;
    })
}

#[derive(Clone, Default)]
struct ManualClock(Rc<Cell<Instant>>);

impl ManualClock {
    fn advance(&self, by: Duration) {
        self.0.set(self.0.get() + by);
    }
}

impl Clock for ManualClock {
    fn now(&self) -> Instant {
        self.0.get()
    }
}

/// Remembers the first byte (the issue number) of each frame shown.
#[derive(Clone, Default)]
struct Screen(Rc<RefCell<Vec<u8>>>);

impl FrameDisplay for Screen {
    type Error = Infallible;

    fn show(&mut self, frame: &Frame) -> Result<(), Infallible> {
        assert!(frame.iter().all(|b| *b == frame[0]), "frame arrived intact");
        self.0.borrow_mut().push(frame[0]);
        Ok(())
    }
}

const BACK: ButtonLevels = ButtonLevels {
    forward: false,
    back: true,
    trigger: false,
};
const FORWARD: ButtonLevels = ButtonLevels {
    forward: true,
    back: false,
    trigger: false,
};
const TRIGGER: ButtonLevels = ButtonLevels {
    forward: false,
    back: false,
    trigger: true,
};

#[test]
fn comic_service_speaks_the_server_api() {
    let server = published(&[1, 2], Some(2));
    let mut comics = service(&server);
    assert_eq!(comics.latest_issue(), Ok(Issue::new(2)));
    let mut frame = [0u8; FRAME_BYTES];
    comics.fetch(Issue::new(1).unwrap(), &mut frame).unwrap();
    assert_eq!(frame.as_slice(), frame_for(1).as_slice());
    assert_eq!(
        comics.fetch(Issue::new(9).unwrap(), &mut frame),
        Err(ComicApiError::Status(404))
    );
    assert_eq!(comics.trigger(), Ok(()));
    assert_eq!(server.state().triggers, 1);
    assert_eq!(
        server.state().requests,
        [
            "GET /api/comics/current HTTP/1.1",
            "GET /api/comics/1/bin HTTP/1.1",
            "GET /api/comics/9/bin HTTP/1.1",
            "POST /api/trigger HTTP/1.1",
        ]
    );
}

#[test]
fn comic_service_handles_an_empty_server_and_a_dead_one() {
    let empty = published(&[], None);
    assert_eq!(service(&empty).latest_issue(), Ok(None));

    let config = HttpConfig {
        connect_timeout: Duration::from_secs(1),
        recv_timeout: Duration::from_secs(1),
    };
    let mut dead = HttpComicService::new(
        HttpClient::new(StdConnector, "x", support::closed_port(), config),
        Ipv4Addr::LOCALHOST,
    );
    assert!(matches!(
        dead.latest_issue(),
        Err(ComicApiError::Http(HttpError::Connect(_)))
    ));
}

#[test]
fn viewer_follows_the_server_and_the_buttons() {
    let server = published(&[1, 2, 4, 5], Some(5));
    server.state().reveal_on_trigger = true;
    let screen = Screen::default();
    let buttons = ScriptedButtons::default();
    let clock = ManualClock::default();
    let mut frame = [0u8; FRAME_BYTES];
    let mut viewer = ComicViewer::new(
        service(&server),
        screen.clone(),
        buttons.clone(),
        clock.clone(),
        &mut frame,
        ViewerConfig::default(),
    );

    viewer.tick();
    assert_eq!(*screen.0.borrow(), [5], "latest shown at boot");

    buttons.press(BACK);
    viewer.tick();
    viewer.tick();
    buttons.press(BACK);
    viewer.tick();
    viewer.tick();
    assert_eq!(
        *screen.0.borrow(),
        [5, 4, 2],
        "issue 3 is missing and skipped"
    );

    buttons.press(FORWARD);
    viewer.tick();
    viewer.tick();
    assert_eq!(*screen.0.borrow(), [5, 4, 2, 4]);

    clock.advance(Duration::from_secs(60));
    viewer.tick();
    viewer.tick();
    assert_eq!(
        *screen.0.borrow(),
        [5, 4, 2, 4, 5],
        "snapped back to latest"
    );

    buttons.press(TRIGGER);
    viewer.tick();
    assert_eq!(server.state().triggers, 1);
    assert_eq!(
        *screen.0.borrow(),
        [5, 4, 2, 4, 5, 6],
        "triggered comic shown on the trigger tick"
    );
    clock.advance(Duration::from_secs(2));
    viewer.tick();
    assert_eq!(
        *screen.0.borrow(),
        [5, 4, 2, 4, 5, 6],
        "no re-flash on the catch-up poll"
    );
}

struct Localhost;

impl HostResolver for Localhost {
    fn resolve(&self, host: &str) -> Option<Ipv4Addr> {
        (host == "comack-server").then_some(Ipv4Addr::LOCALHOST)
    }
}

fn uplink(port: u16) -> WebSocketUplink<'static, StdConnector, Localhost> {
    WebSocketUplink::new(
        StdConnector,
        ServerLocator::new(Localhost, "comack-server"),
        port,
        "/ws/audio",
        WsConfig::default(),
    )
}

/// What the microphone produced, for comparison with what the server got.
fn expected_audio(chunks: usize, chunk: usize) -> Vec<u8> {
    let mut mic = SyntheticMicrophone::new(
        16_000,
        Duration::from_millis(50),
        Duration::from_millis(50),
        false,
    );
    let mut buf = vec![0i16; chunk];
    let mut bytes = Vec::new();
    for _ in 0..chunks {
        mic.read(&mut buf).unwrap();
        bytes.extend(buf.iter().flat_map(|s| s.to_le_bytes()));
    }
    bytes
}

fn streamer_mic() -> SyntheticMicrophone {
    SyntheticMicrophone::new(
        16_000,
        Duration::from_millis(50),
        Duration::from_millis(50),
        false,
    )
}

#[test]
fn streamer_delivers_the_microphone_audio_intact() {
    let server = FakeServer::start(|_| {});
    let mut buffer = vec![0i16; 1600];
    let mut streamer = AudioStreamer::new(
        streamer_mic(),
        uplink(server.port),
        &mut buffer,
        StreamerConfig::default(),
    );
    for _ in 0..5 {
        streamer.tick();
    }
    server.wait_for(WAIT, |s| s.audio_frames == 5);
    let s = server.state();
    assert_eq!(s.ws_sessions, 1);
    assert_eq!(s.unmasked_frames, 0, "client frames are masked");
    assert_eq!(s.audio, expected_audio(5, 1600));
}

#[test]
fn streamer_answers_pings() {
    let server = FakeServer::start(|s| s.ping_after = Some(1));
    let mut buffer = vec![0i16; 160];
    let mut streamer = AudioStreamer::new(
        streamer_mic(),
        uplink(server.port),
        &mut buffer,
        StreamerConfig::default(),
    );
    streamer.tick();
    server.wait_for(WAIT, |s| s.audio_frames == 1);
    // The ping is picked up after a later send.
    for _ in 0..50 {
        streamer.tick();
        if !server.state().pongs.is_empty() {
            break;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    server.wait_for(WAIT, |s| s.pongs == [b"ping".to_vec()]);
    assert!(streamer.is_connected());
}

#[test]
fn streamer_reconnects_after_the_server_hangs_up() {
    let server = FakeServer::start(|s| s.close_after = Some(2));
    let mut buffer = vec![0i16; 160];
    let mut streamer = AudioStreamer::new(
        streamer_mic(),
        uplink(server.port),
        &mut buffer,
        StreamerConfig::default(),
    );
    for _ in 0..200 {
        streamer.tick();
        if server.state().ws_sessions >= 2 && server.state().audio_frames >= 3 {
            break;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    let s = server.state();
    assert!(s.ws_sessions >= 2, "reconnected");
    assert!(s.audio_frames >= 3, "streaming resumed");
}

#[test]
fn streamer_backs_off_while_the_server_is_down() {
    let mut buffer = vec![0i16; 160];
    let mut streamer = AudioStreamer::new(
        streamer_mic(),
        uplink(support::closed_port()),
        &mut buffer,
        StreamerConfig::default(),
    );
    assert_eq!(streamer.tick(), Duration::from_secs(2));
    assert!(!streamer.is_connected());
}
