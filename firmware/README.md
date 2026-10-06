# Comack firmware (Rust)

Firmware for the Seeed **reTerminal E1001** (ESP32-S3, 7.5" 800x480 e-paper,
PDM microphone). It joins the tailnet as a native Tailscale node, streams room
audio to the Comack server, and shows the latest comic, with the front buttons
for browsing older issues and firing the trigger.

## Layout

```
firmware/
├── Cargo.toml              host workspace (core + simulator)
├── crates/
│   ├── comack-core/        no_std, no alloc, no unsafe: all the logic
│   │   └── src/
│   │       ├── domain/       entities and pure rules
│   │       ├── application/  use cases + ports (traits)
│   │       └── adapters/     HTTP, WebSocket, JSON, e-paper driver, buttons
│   └── comack-sim/         std adapters + desktop simulator + end-to-end tests
├── esp/                    ESP32-S3 binary (own workspace, Xtensa toolchain)
│   └── src/                ESP-IDF/MicroLink adapters + composition root
└── components/             vendored C: MicroLink (Tailscale), wireguard_lwip, cJSON
```

## Architecture

Clean Architecture, with dependencies pointing inward only:

| Layer | Where | Contents |
|---|---|---|
| Domain | `comack-core::domain` | `Frame`, `Issue`, `Navigator` (browse and snap-back rules), `ButtonPanel` (edge detection), `StreamStats`/`PeakMeter`, `NetworkRotation` |
| Application | `comack-core::application` | `ComicViewer` and `AudioStreamer` use cases; ports `ComicSource`, `ComicTrigger`, `FrameDisplay`, `ButtonInput`, `Clock`, `AudioSource`, `AudioUplink`/`AudioSession` |
| Interface adapters | `comack-core::adapters` | `HttpComicService` (HTTP/1.1), `WebSocketUplink` (RFC 6455), `Epd7in5` (embedded-hal 1.0 SPI/GPIO), `ActiveLowButtons`, all written against a small `TcpConnector`/`TcpStream`/`HostResolver` boundary |
| Frameworks and drivers | `esp/`, `comack-sim` | MicroLink FFI (`Tailnet`), ESP-IDF Wi-Fi, I2S PDM, SPI and GPIO; or `std::net`, a PBM file and a synthetic microphone |

SOLID in practice:
- **Single responsibility:** each port and adapter does one job. For example, `Navigator` only decides *what* to show, and `ComicViewer` only orchestrates.
- **Open/closed:** a new transport or display is a new adapter. The use cases don't change.
- **Liskov substitution:** the simulator, the test doubles and the device all satisfy the same port contracts, and the tests run against each of them.
- **Interface segregation:** ports are small. `ComicSource` and `ComicTrigger` are separate traits.
- **Dependency inversion:** the use cases depend on traits. Concrete types are chosen only in the two composition roots (`esp/src/main.rs`, `comack-sim/src/main.rs`).

Use cases never sleep. Each `tick()` does one step of work and returns how long to wait before the next one. Scheduling lives in the composition root, so every step is deterministic under test.

## Testing

```sh
cd firmware
cargo test --workspace                                    # unit, property and end-to-end tests
cargo clippy --workspace --all-targets -- -D warnings     # pedantic lints are on
cargo build -p comack-core --target thumbv7em-none-eabihf # proves the core is no_std
```

- **Unit tests** cover every domain rule, both use cases (with in-memory fakes), the protocol encoders and parsers, and the e-paper driver. The driver's command sequence, reset pulse, pixel inversion, chunking and BUSY timeout are checked against recording SPI/GPIO fakes.
- **Property tests** (`proptest`) check that the HTTP body is identical however the response is split across reads, that WebSocket frames round-trip at any length, that `issue_number` parses for any `u32`, that parsers never panic, and that the peak level is computed correctly.
- **End-to-end tests** (`comack-sim/tests`) run the real use cases and adapters over loopback sockets against a fake Comack server that speaks HTTP and WebSocket. They also launch the simulator binary and check the PBM file it writes.

## Desktop simulator

Runs the real core against a live server. It streams synthetic speech and silence, and writes the screen to a PBM image:

```sh
cargo run -p comack-sim -- --host comack-server --port 8000 --out screen.pbm
# type f / b / t + Enter for forward / back / trigger
```

## Building and flashing the device

One-time setup:

```sh
cargo install espup ldproxy espflash
espup install          # Xtensa Rust toolchain ("esp" channel)
. ~/export-esp.sh
```

Then:

```sh
cd firmware/esp
cp src/secrets.rs.example src/secrets.rs   # Wi-Fi networks + Tailscale auth key
cargo run --release                        # builds ESP-IDF v5.5.5 on first run, flashes, opens the monitor
```

`esp-idf-sys` builds the vendored C components in `components/`. It also generates Rust bindings for `microlink.h` as `esp_idf_svc::sys::microlink`.

## Board wiring

| Function | GPIO |
|---|---|
| PDM mic CLK / DIN / power enable | 42 / 41 / 38 |
| E-paper SCK / MOSI / CS / DC / RST / BUSY (active low) | 7 / 9 / 10 / 11 / 12 / 13 |
| Buttons: forward (right white) / back (left white) / trigger (green), active low | 4 / 5 / 3 |

## Behaviour compared with the C++ firmware

The port keeps the C++ firmware's timings: poll every 10 s, re-poll 2 s after a trigger, snap back after 60 s of browsing, sample buttons every 50 ms, send 100 ms audio chunks, report every 50 chunks, and back off 2 s on reconnect. It also keeps the proven e-paper init sequence and the Wi-Fi rotation. These things changed on purpose:

- **HTTP status codes are checked.** A 404 from `/api/comics/current` means "nothing revealed yet". A frame only counts if the status is 200 and exactly 48 000 bytes arrive.
- **The peak meter handles `i16::MIN` (−32768).** In C, negating it overflowed.
- **The JSON parser accepts whitespace around `:`.**
- **WebSocket frames use the 64-bit length form above 64 KiB.** C only had the 16-bit form. Audio chunks never reach that size.
- **Wi-Fi also rotates when joining a network fails,** not only on disconnect.
- **ESP-IDF is v5.5.5** (was 5.3.1), the version esp-idf-svc 0.53 is built and tested against.
- **The app partition is 3 MB** (`partitions.csv`, flashed by espflash). The C build used the IDF's 1.5 MB single-app layout.
