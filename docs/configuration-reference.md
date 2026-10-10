# Configuration reference

Every server setting, derived from the validated settings model in
[`server/config.py`](../server/config.py) (the source of truth — field names
are the environment variable names) and mirrored by [`.env.example`](../.env.example).
Grouped like `.env.example`.

## How configuration works

- **Precedence.** Real process environment variables (systemd unit) win over
  the repo-root `.env` file — also inside the Compose container, which
  bind-mounts that file — which wins over the built-in defaults below.
- **Where to set things.** `.env` in the repo root (copy `.env.example` or a
  [preset](../presets/)), the dashboard's Settings page, or `PATCH /api/config`
  — all three persist to `.env` and produce the same validated result. A value
  held by a real environment variable keeps winning over `.env`; the PATCH
  response includes a note when that is the case.
- **Validation.** Invalid values fail startup — or the PATCH — with a readable
  message naming the variable and rejected value, e.g.
  `COMIC_MIN_MINUTES = 5000: Value error, COMIC_MIN_MINUTES (5000) must not exceed COMIC_MAX_MINUTES (1440)`.
- **Hot vs restart.** `restart`-marked fields are bound when the process (or a
  cached model) starts; PATCH accepts and persists them but flags them
  `restart_required`. Everything else applies immediately (`applied`);
  cadence/loop fields take effect on their next scheduler tick, and `FLUX_*`
  fields on the next render (the render pipeline is released between comics).
- **Unknown variables.** Comack-looking env vars that match no setting
  (`LLM_API_KEY`, a misspelled `COMIC_…`, …) log a warning at startup, with a
  "did you mean" suggestion when one is close. `DISPLAY_WIDTH`/`HEIGHT` are
  fixed hardware constants and are ignored if set.
- **Secrets.** Secret-shaped fields (`HF_TOKEN`, `WEB_TOKEN`, `SECRET_PHRASES`)
  are masked in `GET /api/config` and the settings page; add
  `?include_secrets=true` to reveal, or use the settings page's "reveal
  secrets" toggle.

## Server network bindings

| Setting | Type | Default | Bounds | Restart | Meaning |
|---|---|---|---|---|---|
| `HOST` | str | `0.0.0.0` | — | restart | Address the FastAPI server binds to. |
| `PORT` | int | `8000` | 1–65535 | restart | Port for the FastAPI server (dashboard, WebSocket audio, comic API). |

## Dashboard & access

| Setting | Type | Default | Bounds | Restart | Meaning |
|---|---|---|---|---|---|
| `ONBOARDED` | bool | `false` | — | hot | Set automatically when the dashboard's first-run wizard completes. Set to false to see it again. |
| `WEB_TOKEN` | str | `""` | — | hot | Optional bearer token for browser-facing endpoints. Empty = open access (assumes a single-user network like a tailnet). See [the exact auth split](self-hosting.md#access-token-web_token) in self-hosting. |

## Devices & identity (no settings)

Multiple displays can share one server — each device gets its own audio
pipeline, story arc, pacing state, and current comic. Device identity is a
database record, not a configuration variable: there is deliberately nothing
to set here.

- **Registration.** Create devices on the dashboard's settings page (Devices
  card) or `POST /api/devices {"name": "Living Room"}`. The response shows
  the device's `id` and `token` exactly once — the token is stored hashed
  and compared constant-time. `PATCH /api/devices/<id>` renames; `GET
  /api/devices` lists devices with their live connection count and
  last-seen time.
- **On the wire.** A client presents identity as query parameters —
  `?device_id=<id>&device_token=<token>` — on `/ws/audio`, the
  `current`-comic reads, and `/api/trigger` (the same transport the event
  feed's `?token=` already uses; browser and firmware clients cannot set
  headers). Three outcomes, no exceptions: a valid pair binds the
  connection to that device; **no** identity binds the auto-created
  `default` device, so pre-existing deployments and tokenless clients keep
  working unchanged; a **wrong** id or token is rejected — HTTP 401 on the
  read endpoints, and the audio WebSocket refuses the connection (close
  code `4401`; a raw client sees the pre-accept denial as HTTP 403).
  Wrong identity never falls back to `default`.
- **Scoped vs shared.** Per device: VAD (noise floor + silence clock),
  trigger window, transcript batch, story arc, pacing key, dispatcher
  slot, and the displayed-comic pointer — each room hears, grows, and
  shows its own comic. Shared: the Whisper model, LLM and image settings,
  the global issue-number sequence (per-arc `series_issue` is what a
  device's banner shows), and the GPU generation lock, which serializes
  any two devices that come due at once.
- **Upgrades.** On first boot after upgrading, every pre-existing arc,
  transcript, and comic is backfilled to the `default` device. No files
  move and no setting changes; a single-device deployment behaves exactly
  as before.

Clients carry the pair as build-time constants (firmware: `DEVICE_ID` /
`DEVICE_TOKEN` in `crates/comack-core/src/config.rs`) or CLI flags
(simulator: `--device-id` / `--device-token`; absent = legacy behavior).

## LLM (OpenAI-compatible)

| Setting | Type | Default | Bounds | Restart | Meaning |
|---|---|---|---|---|---|
| `LLM_URL` | str | `""` | — | hot | OpenAI-compatible chat endpoint (llama.cpp, vLLM, Ollama…), e.g. `http://host:8080/`. **Empty disables story mining** — the pipeline stops before mining and no comic is generated. |
| `LLM_MODEL` | str | `""` | — | hot | Model name sent with each request (llama.cpp ignores it). |
| `MINER_LLM_URL` | str | `""` | — | hot | Separate small, fast server for the per-sentence miner pass (empty = `LLM_URL`). |

## Hugging Face

| Setting | Type | Default | Bounds | Restart | Meaning |
|---|---|---|---|---|---|
| `HF_TOKEN` | str | `""` | — | restart | Hugging Face token for gated models (**secret: masked in /api/config**). Falls back to `~/.cache/huggingface/token`. |

## Whisper speech-to-text

| Setting | Type | Default | Bounds | Restart | Meaning |
|---|---|---|---|---|---|
| `WHISPER_MODEL_SIZE` | str | `base.en` | — | restart | Model size/name passed to faster-whisper (e.g. `tiny.en`, `base.en`, `small.en`). Sizes and download footprints: [troubleshooting](troubleshooting.md#whisper-model-downloads). |
| `WHISPER_DEVICE` | str enum | `auto` | `cuda` / `cpu` / `auto` | restart | Device for Whisper. |
| `WHISPER_MIN_AVG_LOGPROB` | float | `-1.0` | ≤ 0 | hot | Skip transcribed segments with avg logprob below this (hallucination filter). |
| `WHISPER_MAX_COMPRESSION` | float | `2.4` | ≥ 0 | hot | Skip segments whose compression ratio exceeds this (too repetitive). |
| `WHISPER_MAX_NO_SPEECH` | float | `0.6` | 0–1 | hot | Skip segments that are probably not speech (above this) AND low-confidence. |

## Comic cadence & triggers

| Setting | Type | Default | Bounds | Restart | Meaning |
|---|---|---|---|---|---|
| `SNEAKY_SILENCE_SECONDS` | float | `0` | 0–86400 | hot | Sneaky mode: seconds of room silence before a new comic is shown (0 = show as soon as rendered). |
| `SECRET_PHRASES` | list of str (comma-separated) | `alpha protocol` | — | hot | Spoken phrases that force an immediate refresh (comma-separated, case-insensitive; also masked as secret-shaped). Accepts a JSON list too. |
| `COMIC_MIN_MINUTES` | float | `5` | 0–10080 | hot | Never generate a new comic sooner than this many minutes after the last. Must be ≤ `COMIC_MAX_MINUTES` (validated as a pair). |
| `COMIC_MAX_MINUTES` | float | `1440` | 0–10080 | hot | Never wait longer than this many minutes between comics (1 day default). |
| `DEFAULT_LINES_WANTED` | int | `8` | 1–30 | hot | How many scrambled lines each comic should collect (feeds the LLM prompt). |
| `PROCESS_INTERVAL_MINUTES` | float | `2` | 0.1–1440 | hot | Buffered speech is transcribed, scrambled and mined this often (trigger phrases check continuously). |

## Privacy scramble

| Setting | Type | Default | Bounds | Restart | Meaning |
|---|---|---|---|---|---|
| `POS_SCRAMBLE_RATE` | float | `0.25` | 0–1 | hot | Fraction of eligible words swapped for same-POS stand-ins before anything is stored or sent to the LLM. |

## Story arcs

| Setting | Type | Default | Bounds | Restart | Meaning |
|---|---|---|---|---|---|
| `ARC_MIN_PANELS` | int | `12` | 1–120 | hot | Narrative arc length lower bound, in panels. Must be ≤ `ARC_MAX_PANELS` (validated as a pair). |
| `ARC_MAX_PANELS` | int | `60` | 1–120 | hot | Narrative arc length upper bound, in panels. |

## Image generation (local diffusers or external API)

| Setting | Type | Default | Bounds | Restart | Meaning |
|---|---|---|---|---|---|
| `FLUX_MODEL_ID` | str | `stabilityai/sd-turbo` | — | hot* | Local model (defaults to fast 1-step SD-Turbo; `black-forest-labs/FLUX.1-schnell` for quality on GPU). |
| `FLUX_DEVICE` | str enum | `cuda` | `cuda` / `mps` / `cpu` | hot* | Device for local image generation. |
| `FLUX_INFERENCE_STEPS` | int | `1` | 1–50 | hot* | Diffusion steps per panel (SD-Turbo: 1; FLUX.1-schnell: 4). |
| `FLUX_GUIDANCE_SCALE` | float | `0.0` | ≥ 0 | hot* | Guidance scale for local generation (0 for SD-Turbo/schnell). |
| `FLUX_GGUF_URL` | str | `https://huggingface.co/city96/FLUX.1-schnell-gguf/blob/main/flux1-schnell-Q4_K_S.gguf` | — | hot* | Where to fetch the GGUF-quantized FLUX transformer from (downloaded once, cached). |
| `FLUX_MIN_FREE_VRAM_GB` | float | `10.5` | ≥ 0 | hot* | Pause generation (and wait) when free VRAM drops below this, so the LLM can breathe. |
| `FLUX_VRAM_WAIT_SECONDS` | float | `180` | ≥ 0 | hot* | How long to wait for VRAM before giving up on a panel. |
| `FLUX_API_URL` | str | `""` | — | hot* | External image API (OpenAI-compatible `/v1/images/generations`). Set = **remote-only**: panels render on the API and the local diffusers stack is never touched. Empty = render locally. |
| `FLUX_API_KEY` | str | `""` | — | hot* | Bearer token for the external image API, sent on every remote render and the connectivity probe while `FLUX_API_URL` is set (masked in GET /api/config). |
| `FLUX_API_MODEL` | str | `""` | — | hot* | Model name sent to the external image API in remote-only mode (empty = omit the field, the provider's default model is used). |

\* "hot" with an asterisk: the FLUX pipeline is released after every comic, so
`FLUX_*` changes apply at the **next render**, not mid-render. In the PATCH /
settings-page sense they are still `applied` immediately.

**Remote-only mode.** Setting `FLUX_API_URL` (with or without a trailing `/v1`)
makes the image API the *only* rendering backend: each panel is one
OpenAI-compatible `POST /v1/images/generations` request asking for base64 (a
provider that answers with a URL instead is still handled), retried on the
usual panel backoff, with `FLUX_API_KEY` sent as a Bearer token and
`FLUX_API_MODEL` carried as the request's model when set. The local diffusers
stack — torch import, GGUF download, VRAM guard — is never touched, so a box
with no GPU and no model cache renders fine. If the API never answers, the
comic fails loudly (`PanelArtFailed`; nothing is published) and remote calls
show up in `/api/prompts` like local renders do. Empty `FLUX_API_URL` is the
unchanged local path. The Docker image still ships the AI stack this round;
slimming it for remote-only installs is future work.

## Display (fixed hardware constants)

`DISPLAY_WIDTH` = 800, `DISPLAY_HEIGHT` = 480 — the reTerminal E1001 panel
size. **Not env-overridable**: they are declared as class constants, and
setting them in the environment logs an info line and ignores the value.
Listed in `.env.example` for reference only.

## Related, but not settings

| Variable | Read by | Meaning |
|---|---|---|
| `HF_HUB_OFFLINE` | huggingface_hub at model load | Set to `1` once models are cached to skip the per-load huggingface.co revision check. Unset when a new model needs downloading. Comack knows it (no unknown-var warning) but does not consume it. |

## The settings API

- `GET /api/config` — resolved settings, secrets masked;
  `?include_secrets=true` reveals.
- `GET /api/config/presets` — the bundled presets as `{name, values}` (what
  the onboarding Image cards prefill).
- `PATCH /api/config` — accepts a JSON object of changes, validates against
  the same model as startup, applies hot fields, flags the rest
  `restart_required`, persists to `.env`, and notes where a real environment
  variable outranks `.env`:

```sh
curl -X PATCH localhost:8000/api/config \
  -H 'Content-Type: application/json' -d '{"COMIC_MIN_MINUTES": 10}'
```

The settings page in the dashboard covers the same ground with per-field
descriptions, `restart` chips, and **Test connection** probes — the page is
generated from the same settings model, so it cannot drift from this table.

## Verification notes

Field-by-field, this page was checked against `server/config.py` at commit
`82360c6` (names, types, defaults, bounds, descriptions) and against
`server/config_api.py` (the restart-required set: `HOST`, `PORT`,
`WHISPER_MODEL_SIZE`, `WHISPER_DEVICE`, `HF_TOKEN`; the FLUX next-render
behavior; masking). The paired-bound validators, masking, unknown-var
warnings, and the `applied` / `restart_required` PATCH responses were
exercised live on a running server, as were `GET /api/config` and the
settings page. `.env.example` was diffed against this table field by field.
The devices section was verified against the multi-device branch: the three
identity outcomes and the scoped/shared split are pinned by
`tests/test_two_device_acceptance.py` and were exercised live with two
simulators streaming against one server.
