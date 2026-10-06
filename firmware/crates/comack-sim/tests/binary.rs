//! Runs the `comack-sim` binary against the fake server.

mod support;

use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use support::{frame_for, FakeServer};

#[test]
fn simulator_renders_the_latest_comic_to_a_pbm_file() {
    let server = FakeServer::start(|s| {
        s.issues.insert(3, frame_for(3));
        s.current = Some(3);
    });
    let dir = std::env::temp_dir().join(format!("comack-sim-bin-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let out = dir.join("screen.pbm");

    let mut child = Command::new(env!("CARGO_BIN_EXE_comack-sim"))
        .args([
            "--host",
            "127.0.0.1",
            "--port",
            &server.port.to_string(),
            "--out",
        ])
        .arg(&out)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();

    let deadline = Instant::now() + Duration::from_secs(10);
    let pbm = loop {
        if let Ok(bytes) = std::fs::read(&out) {
            break bytes;
        }
        assert!(Instant::now() < deadline, "no screen written");
        std::thread::sleep(Duration::from_millis(20));
    };
    server.wait_for(Duration::from_secs(10), |s| s.audio_frames > 0);
    child.kill().unwrap();
    child.wait().unwrap();

    let header = b"P4\n800 480\n";
    assert_eq!(&pbm[..header.len()], header);
    assert!(
        pbm[header.len()..].iter().all(|b| *b == !3u8),
        "issue 3, inverted for PBM"
    );
    std::fs::remove_dir_all(dir).unwrap();
}

#[test]
fn simulator_rejects_bad_arguments() {
    let status = Command::new(env!("CARGO_BIN_EXE_comack-sim"))
        .arg("--bogus")
        .stderr(Stdio::null())
        .status()
        .unwrap();
    assert_eq!(status.code(), Some(2));
}
