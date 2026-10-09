# Contributing to Comack

Thanks for taking a look. This project is docs-and-tests friendly: most work
lands as small, focused pull requests. Here's how to get set up.

## Server (Python)

Requires Python 3.11+.

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e .            # light core: server + tests, no torch/diffusers
pip install -e '.[dev]'     # adds pytest
```

The heavy AI dependencies (torch, diffusers, faster-whisper, …) are **optional**:

```sh
pip install -e '.[comack-server-ai]'   # local Whisper + FLUX rendering
```

The server imports them lazily inside the transcriber and image generator, so
light-core installs can import every `server` module and run the full test
suite without them. (You only need the extra to run transcription/rendering
locally; without it, point `FLUX_API_URL` at an external renderer or run tests
that don't exercise those paths.)

`requirements.txt` stays as the full pinned set used by the `comack-server`
Docker/systemd deployment.

Run the tests from the repo root:

```sh
pytest
```

Server configuration: `cp .env.example .env` and edit. Every setting is
documented there; defaults mirror `server/config.py`.

## Firmware (Rust)

The firmware is a Rust workspace with a vendored C layer (ESP-IDF). Host-side
test commands (no ESP toolchain needed):

```sh
cd firmware
cargo test --workspace                                    # unit, property and end-to-end tests
cargo clippy --workspace --all-targets -- -D warnings     # pedantic lints are on
cargo fmt --all --check                                   # CI enforces formatting
cargo build -p comack-core --target thumbv7em-none-eabihf # proves the core is no_std
```

ESP32-S3 build and flash instructions (Xtensa toolchain, ESP-IDF) are in
[firmware/README.md](firmware/README.md). CI builds the ESP target on every
firmware change; you can push a PR and let CI do the cross-compile check.

## Pull requests

- **Green CI.** The `firmware` workflow must pass for firmware paths; run
  `pytest` locally for server changes.
- **Small PRs.** One behavior or fix per PR, with a description that explains
  the *why* and how you verified it.
- **Conventional commits.** `feat:`, `fix:`, `docs:`, `refactor:`, `test:`,
  `build:`, `chore:` — with a scope where it helps (`feat(server): …`).
- **Mind the privacy posture.** Changes that move data across a boundary
  (endpoints, uploads, telemetry) need a clear justification in the PR body.
- **Don't commit secrets or personal infrastructure.** Hostnames, usernames,
  API keys and `.env` values stay out of the repo (`.env` and
  `firmware/esp/src/secrets.rs` are gitignored for this reason).
- Licensing: by contributing, you agree your contributions are licensed under
  the Apache License 2.0 (see [LICENSE](LICENSE)).
