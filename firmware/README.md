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
| Domain | `comack-core::domain` | `Frame`, `Issue`, `Navigator` (browse and snap-back rules), `ButtonPanel` (edge detection), `StreamStats`/`PeakMeter`, `NetworkRotation`, `DeviceConfig` (bounded runtime configuration + its versioned codec) |
| Application | `comack-core::application` | `ComicViewer` and `AudioStreamer` use cases; ports `ComicSource`, `ComicTrigger`, `FrameDisplay`, `ButtonInput`, `Clock`, `AudioSource`, `AudioUplink`/`AudioSession`, `DeviceConfigStore` (+ the stored-or-fallback resolver) |
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
cargo run -p comack-sim -- --host localhost --port 8000 --out screen.pbm
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
cp src/secrets.rs.example src/secrets.rs   # compile-time fallback: Wi-Fi networks + Tailscale auth key
cargo run --release                        # builds ESP-IDF v5.5.5 on first run, flashes, opens the monitor
```

The compile-time fallback carries the server address too: `SERVER.host` in
`firmware/crates/comack-core/src/config.rs` defaults to `localhost` — right
for the desktop simulator, not for a real device. Set your server's hostname
there before flashing an ESP target (the value is compiled in; a
runtime-stored config on the device overrides it later).

`esp-idf-sys` builds the vendored C components in `components/`. It also generates Rust bindings for `microlink.h` as `esp_idf_svc::sys::microlink`.

## Provisioning a device

The first flash always needs USB. After that, the settings live on the device
and the portal changes them with nothing but a phone. The same fields
(Wi-Fi SSID and password, server address, device name, optional Tailscale
auth key) feed every path:

- **On-device portal** (primary): whenever the device has no stored
  configuration and no usable compile-time fallback — or you hold the setup
  button for 3 seconds — it broadcasts a `comack-setup` Wi-Fi network, draws
  instructions on the e-paper, and stores everything on-chip when you save.
  Network changes later need no cable.
- **`scripts/provision-device.sh`** (assisted first flash): since the first
  flash needs a cable anyway, this script collects the same fields at the
  computer and drives the build-and-flash in one step. It renders
  `firmware/esp/src/secrets.rs` — the compile-time **fallback** the firmware
  boots with until a runtime-stored config exists.

### The on-device portal

When the portal runs, the e-paper shows the steps: join the Wi-Fi network
`comack-setup`, then open <http://192.168.4.1> in a phone or laptop browser.
The page collects:

| Field | Meaning | Validation before anything is saved |
|---|---|---|
| Wi-Fi SSID | your home network, ≤ 32 characters (802.11 limit) | non-empty, bounded |
| Wi-Fi password | WPA2 passphrase, ≤ 64 characters | bounded |
| Server address | hostname or `ip:port` (`:port` optional, default 8000) | non-empty, bounded; the form never echoes stored secrets back |
| Device name | shown on the server's dashboard | blank uses the default `comack-display` |
| Tailscale auth key | optional, `tskey-auth-…` | **empty is legitimate**: the device runs LAN-only (no tailnet); the server must then be reachable on the same network by DNS name or literal IP |

On save the firmware validates, writes the on-chip NVS store, closes the
`comack-setup` network, and joins your Wi-Fi; the e-paper shows the connecting
steps and, if the server cannot be reached, a screen that names the retry
loop. Rejected submissions save nothing — the config is never left half-set
— and the portal stays up for a corrected entry.

**The setup button is the left (back) button, GPIO 5.** Holding it for 3
seconds from any state — including normal operation — restarts the device
into the portal (keep holding through the brief reboot; the boot checks the
same hold). The instructions and error screens name it.

MicroLink initialises from the stored key when one is set; with an empty key
the device never joins a tailnet and talks plain LAN TCP to the server. The
portal is comack-owned end to end: the vendored MicroLink config UI stays
compiled out (it is unreachable before Wi-Fi exists and manages the wrong
Wi-Fi list), and MicroLink keeps doing tailnet transport only.

### Manual hardware checklist

What follows is **not automatable in this repo** — there is no QEMU or
on-target test rig, so these checks are the owner's, on hardware:

1. **Fresh device:** flash with no stored config and an empty
   `secrets.rs` network list → the device boots into the portal and the
   e-paper shows the join instructions.
2. **Phone join:** join `comack-setup`, open `http://192.168.4.1`, fill the
   form, save → the device leaves AP mode, joins the home Wi-Fi and comes
   online on the dashboard.
3. **Network change without a cable:** hold the left (back) button 3 s →
   the portal returns; join it and enter the new network → the device
   reconnects, still with no cable.
4. **Server-down error screen:** with the device running, stop the server →
   after a connect failure the e-paper shows the unreachable screen naming
   the retry loop; start the server again → the device recovers on its own.
5. **LAN-only mode:** save the form with an empty Tailscale key → the device
   never joins a tailnet and reaches the server over the LAN.

### The assisted-flash script

The script renders `firmware/esp/src/secrets.rs` from the checked-in
`secrets.rs.example`, then runs the same `cargo build --release`
and `espflash flash` as the manual flow above. Run it from the repo root:

```sh
./scripts/provision-device.sh                             # interactive prompts
./scripts/provision-device.sh --ssid HomeNet --password '…' --yes
./scripts/provision-device.sh --help                      # all flags
```

| Flag | Meaning | Default |
|---|---|---|
| `--ssid`, `--password` | Wi-Fi credentials for the device | prompted (password input hidden) |
| `--server` | server hostname or `ip:port` | `localhost:8000` |
| `--name` | device name | `comack-display` |
| `--tskey` | Tailscale auth key (`tskey-auth-…`) | empty — placeholder kept |
| `--port` | serial device for flashing or monitor | auto-detected by espflash |
| `--yes` | never prompt (for scripted use) | — |
| `--no-secrets` | leave `secrets.rs` untouched; build and flash it as-is | — |
| `--no-flash` | render and build only | — |
| `--force` | build and flash even when nothing changed | — |
| `--force-secrets` | overwrite a differing, hand-edited `secrets.rs` | — |
| `--monitor` | attach `espflash monitor` instead of flashing on a no-op run | — |

Behaviour worth knowing:

- **Idempotent.** After a successful flash the script records what it flashed
  in `firmware/esp/.provision-state` (hashes only — no secrets, and
  gitignored). Rerunning with the same values is a no-op; `--force` re-flashes.
- **Hand edits are protected.** If `secrets.rs` exists and differs from both
  the rendered output and the last scripted flash, the script asks before
  overwriting — and refuses when `--yes` suppresses prompts. Override with
  `--force-secrets`, or flash a hand-edited `secrets.rs` as-is with
  `--no-secrets`.
- **Values are Rust-escaped.** Quotes and backslashes render correctly; the
  rendered file is written `0600` and `secrets.rs` is gitignored.
- **Inputs are validated.** SSIDs over 32 characters (the 802.11 limit) and
  server values that are not `host[:port]` or a bare IP are rejected before
  anything is written. A short Wi-Fi password or a key that does not start
  with `tskey-auth-` warns but proceeds.

Two values stay compile-time constants in
`firmware/crates/comack-core/src/config.rs` — `SERVER` and `DEVICE_NAME`. The
script warns when your `--server`/`--name` differ from those defaults; edit
the file before flashing to actually change them. (The runtime config store
moves both on-chip: once a config is saved through the portal, the stored
server and device name win.) Leaving `--tskey` empty
keeps the placeholder: the device will not join a tailnet — after a portal
save it runs LAN-only; with a fallback boot, since `SERVER`
resolves via MagicDNS it will not reach the server by name either — point
`SERVER` at a literal IP for a tailnet-less LAN setup.

Verification notes: executed here — `bash -n` and `shellcheck` clean, plus a
stubbed-toolchain functional suite covering rendering and escaping, no-op
reruns, `--force`, overwrite protection, validation, the exact `espflash`
arguments, and interactive prompts (via pty). Not executed here — a real
ESP-IDF build and device flash need the Xtensa toolchain and hardware; the
flag surface mirrors the manual flow documented above.
## Device configuration

At boot the device resolves one `DeviceConfig`: the Wi-Fi networks to join
(primary first), the server host and port, the device name, the device
identity, and the Tailscale auth key.

1. **Stored configuration first.** The `NvsDeviceConfigStore` adapter reads
   the `device_config` blob in the `comack` NVS namespace. The on-device
   setup portal writes it (see "Provisioning a device" above); until then
   the namespace is simply empty.
2. **Compile-time fallback.** With nothing stored — or the store unreadable —
   the build's `secrets.rs` (networks, device identity, Tailscale key) and
   `config.rs` constants (`localhost`, port 8000, `comack-display`) apply,
   with a logged reason. Already-flashed devices therefore boot exactly as
   before, and `secrets.rs` stays supported.

Both paths run through the same bounded type (`comack_core::domain`:
`DeviceConfig`, at most 5 networks) and the same `DeviceConfigStore` port,
so the store is an in-memory fake in the host tests.

### Device identity on the wire

The identity pair (`device_id` + `device_token`, both empty until
configured) rides on the identity-aware server requests — the audio
WebSocket handshake (`GET /ws/audio`), the comic poll
(`GET /api/comics/current`), and the trigger (`POST /api/trigger`) — as
`?device_id=&device_token=` query parameters, matching the server's device
registry (`server/devices.py`). Empty values keep the pre-identity wire
behavior: the request lines stay bare and the server binds the device to
its auto `default` device. An id without its matching token goes out as-is
and is rejected server-side — never silently downgraded. The per-issue
frame fetch (`GET /api/comics/{n}/bin`) deliberately carries no identity:
issue numbers are a global namespace and the tailnet is the trust
boundary.

### Verification notes

- **Executed:** save/load/clear round-trip and empty-store fallback against
  the in-memory store (`cargo test --workspace`); the versioned codec round-
  trips every bounded field shape; `cargo build -p comack-core --target
  thumbv7em-none-eabihf` proves the config and credential types are no_std.
- **Executed:** the esp32s3 cross-compile builds the NVS adapter and the
  access-point configuration builder (`wifi::access_point_configuration`),
  proving the SoftAP mode API is available and links on this stack.
- **Executed:** text rendering to the e-paper frame — `comack_core::domain`
  `draw_text` renders the printable ASCII set with a 5×7 font, covered by
  unit tests and visually inspected via the simulator's `text_sample` PBM.
- **Not executed (hardware):** an actual NVS read/write cycle on the ESP32-S3
  and switching the radio into access-point mode. Both are on the setup
  portal PR's hardware checklist; no QEMU emulation exists in this repo.

## Board wiring

| Function | GPIO |
|---|---|
| PDM mic CLK / DIN / power enable | 42 / 41 / 38 |
| E-paper SCK / MOSI / CS / DC / RST / BUSY (active low) | 7 / 9 / 10 / 11 / 12 / 13 |
| Buttons: forward (right white) / back (left white, **also the setup button** — hold 3 s for the portal) / trigger (green), active low | 4 / 5 / 3 |
| Onboard LED, active low (LOW = on) | 6 |

## Behaviour compared with the C++ firmware

The port keeps the C++ firmware's timings: poll every 10 s, re-poll 2 s after a trigger while a new comic generates, snap back after 60 s of browsing, sample buttons every 50 ms, send 100 ms audio chunks, report every 50 chunks, and back off 2 s on reconnect. It also keeps the proven e-paper init sequence and the Wi-Fi rotation. These things changed on purpose:

- **The trigger fetches and shows on the press itself.** The C++ firmware waited for the 2 s re-poll; now a successful trigger POST polls, downloads and shows on the same tick. The 2 s cadence remains as catch-up while the server generates, until one cycle past the normal poll interval.

- **Every press reports to the server and lights the onboard LED.** Each dispatched press POSTs `/api/device/buttons` with `{"button": "forward" | "back" | "trigger"}` before the viewer acts on it — the dashboard flashes a matching chip — and the GPIO 6 LED stays lit for 150 ms. Reporting never blocks navigation: transport errors and non-2xx replies are logged and dropped.

- **Presses are latched across blocking work.** The e-paper refresh and comic downloads block the main loop, and the C++ firmware sampled the buttons only between those windows; a press fully contained in one was lost. Falling-edge GPIO interrupts now latch such presses and the next 50 ms sample dispatches exactly one edge per latched press.

- **HTTP status codes are checked.** A 404 from `/api/comics/current` means "nothing revealed yet". A frame only counts if the status is 200 and exactly 48 000 bytes arrive.
- **The peak meter handles `i16::MIN` (−32768).** In C, negating it overflowed.
- **The JSON parser accepts whitespace around `:`.**
- **WebSocket frames use the 64-bit length form above 64 KiB.** C only had the 16-bit form. Audio chunks never reach that size.
- **Wi-Fi also rotates when joining a network fails,** not only on disconnect.
- **ESP-IDF is v5.5.5** (was 5.3.1), the version esp-idf-svc 0.53 is built and tested against.
- **The app partition is 3 MB** (`partitions.csv`, flashed by espflash). The C build used the IDF's 1.5 MB single-app layout.
