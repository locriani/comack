# Comack

[![server](https://github.com/locriani/comack/actions/workflows/server.yml/badge.svg)](https://github.com/locriani/comack/actions/workflows/server.yml)

Comack is a privacy-first appliance that turns room conversation into an
ever-evolving surreal comic strip. A Seeed **reTerminal E1001** (ESP32-S3, 7.5″
800×480 e-paper, PDM microphone) sits in your room, streams audio to a server
you own, and the server transcribes what it hears — filters it through a
part-of-speech word-scramble that makes attribution impossible — and feeds the
scrambled residue to an LLM that mines story material. Stories grow into
multi-panel narrative arcs, get rendered as comics, and appear on the e-paper
display on the wall. You never see raw transcripts; your guests' words become
dream material before anything is stored.

## The pipeline

```
 room audio (PDM mic)
        │  device firmware (Rust, see firmware/README.md)
        │  WebSocket — 100 ms chunks to your server
        ▼
 Voice activity detection ──── silence/noise discarded, never transcribed
        ▼
 Whisper transcription ────── faster-whisper, local
        ▼
 POS scramble ─────────────── stochastic part-of-speech word swap
        │                      (the only text that is stored or sent onward)
        ▼
 LLM mining ───────────────── per-sentence "story material" pass (OpenAI-
        │                      compatible endpoint: llama.cpp, vLLM, Ollama...)
        ▼
 Narrative engine ─────────── material accumulates into arcs of 12–60 panels
        ▼
 Image render ─────────────── local diffusers (SD-Turbo / GGUF FLUX) or an
        │                      external FLUX_API_URL endpoint
        ▼
 Comic compositor ─────────── panels, captions and page layouts (Pillow)
        │
        ├──► e-paper via /api/comics/current.bin   (the wall display)
        └──► web dashboard + reader at http://<server>:8000/
```

| Directory | What lives there |
|---|---|
| `firmware/` | Rust device firmware: `no_std` core, desktop simulator, ESP32-S3 binary, vendored C components ([details](firmware/README.md)) |
| `server/` | Python FastAPI server: VAD, transcription, scramble, mining, narrative, rendering, storage, web UI |
| `simulator/` | Python desktop stand-in for the device hardware (`reterminal_sim.py`) |
| `tests/` | Python test suite (runs without the heavy AI dependencies) |

## Quickstart

### Server via Docker

```sh
git clone https://github.com/locriani/comack && cd comack
cp .env.example .env          # edit: point LLM_URL at an OpenAI-compatible endpoint
docker compose up -d --build
# Dashboard: http://localhost:8000/  (comics, reader, status)
```

The container includes the AI dependencies; on first comic it downloads the
configured image model (SD-Turbo by default). Without an LLM at `LLM_URL` the
pipeline stops before mining — everything else runs.

### Configuration presets

`.env.example` lists every setting with safe defaults. Three presets bundle the
settings that differ per hardware profile — start from the one matching where
the server runs, then edit `LLM_URL`:

| Preset | Pick it when |
|---|---|
| `presets/local-gpu.env` | This machine has a CUDA GPU (~10 GB free VRAM): local FLUX.1-schnell rendering, best quality. |
| `presets/external-api.env` | Images should render on a remote OpenAI-compatible image API (`FLUX_API_URL`); no local GPU needed. |
| `presets/cpu-only.env` | No GPU at all: tiny Whisper + 1-step SD-Turbo on CPU, with a slower cadence to absorb renders. |

```sh
cp presets/local-gpu.env .env   # then edit LLM_URL
```

The running server exposes the same settings: `GET /api/config` returns the
resolved configuration (secret-shaped values like `HF_TOKEN` masked; add
`?include_secrets=true` to reveal), and `PATCH /api/config` accepts a JSON
object of changes — e.g. `curl -X PATCH localhost:8000/api/config
-H 'Content-Type: application/json' -d '{"COMIC_MIN_MINUTES": 10}'`.
Runtime-adjustable fields apply immediately (`"applied"`); fields bound at
startup (model sizes, devices, host/port) come back `"restart_required"` and
are persisted to `.env` so they survive the restart. A value that is also set
as a real environment variable (systemd unit, docker-compose) keeps winning
over `.env` — the PATCH response notes when that's the case.

### Desktop simulator (no hardware needed)

The Python simulator emulates the reTerminal: it streams audio to the server
and shows the current comic in a window.

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e .              # light core — no torch/diffusers needed
uvicorn server.main:app --port 8000
# second terminal:
python simulator/reterminal_sim.py --host localhost --port 8000
```

### The real device

Building and flashing the ESP32-S3 firmware (Xtensa toolchain, ESP-IDF,
Tailscale integration) is documented in [firmware/README.md](firmware/README.md).
Configuration for both server and firmware is covered in
[CONTRIBUTING.md](CONTRIBUTING.md); every server setting is listed in
[.env.example](.env.example).

## Privacy & Ethics

Comack is built to listen to a room, and that demands care. The design
decisions, in plain language:

- **Designed for your own space.** This is an appliance for your home or
  office, pointed at people who know it's there. The microphone is on a device
  you own, streaming to a server you own, storing on a disk you own. There is
  no cloud component and no account anywhere.
- **Local-first.** Audio, transcriptions, story state and comics live in
  `data/` (SQLite plus image files) on the machine running the server. Nothing
  is replicated or phoned home.
- **The POS scramble is built-in deniability.** Before a sentence is stored or
  sent anywhere, most content words are replaced with random same-part-of-speech
  stand-ins ("the cat sat on the mat" might become "the ledger sat under the
  mat"). Sentence structure survives; meaning and attribution do not. What the
  LLM mines — and what ends up on disk — is that scrambled text, not what was
  said.
- **What can leave the machine, and the only things that can.** Three doors,
  all optional, all pointed at endpoints *you* configure:
  1. **`LLM_URL` / `MINER_LLM_URL`** — scrambled text is sent to your
     OpenAI-compatible endpoint. If you host your own LLM, text never leaves
     your network.
  2. **`FLUX_API_URL`** — image prompts (already derived from scrambled text)
     go to an external image API if you configure one. Empty by default means
     rendering happens locally.
  3. **Model downloads** — one-time fetches of Whisper/FLUX weights from
     Hugging Face and NLTK corpora into `~/nltk_data`. Downloads only: your
     audio and text are not part of these requests.
- **Silence is discarded.** Voice activity detection runs first; noise and
  silence are dropped before transcription, not merely ignored.

This is an art project with a wiretap's surface area, handled accordingly: run
it where everyone present can see the e-paper, and tell your guests what it is.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, test commands and PR
expectations. Where the project is headed is in [ROADMAP.md](ROADMAP.md).

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE) (which covers the
vendored firmware components: MicroLink, wireguard_lwip, cJSON).
