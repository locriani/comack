# Troubleshooting

Symptoms first, causes after. Everything here reflects the current behavior
of the code at commit `82360c6`; the probe-failure messages are quoted from a
running server (they are exact strings — you can search this page for what
your screen shows).

## Server won't start

**`OSError: [Errno 98] Address already in use` (or "port is already
allocated")** — something else has `PORT`. Find it (`ss -tlnp | grep 8000`),
stop it, or change the port: `PORT=8001` in `.env` (or the unit/compose file)
and restart — `PORT` is a restart-required setting, not hot-adjustable.
The same applies to whatever reverse proxy sits in front.

**`Comack configuration is invalid: <VAR> = <value>: …` at startup** — the
pydantic-settings validation refused a value (out of bounds, bad enum, or
inverted pair). The message names the variable and the rejected value; fix it
in the file the error's stack implies (`.env`, the environment, or the unit).
Bounds for every variable: [configuration reference](configuration-reference.md).
Example — inverting a validated pair prints both names:

```
COMIC_MIN_MINUTES = 5000: Value error, COMIC_MIN_MINUTES (5000) must not
exceed COMIC_MAX_MINUTES (1440)
```

**`Unknown setting XYZ in the environment` warning** — a variable that looks
Comack-related (prefixes like `LLM_`, `FLUX_`, `COMIC_`, …) matches no
setting. Harmless, but it's usually a typo or a name from an older release;
the log suggests the closest real name when there is one. Current names:
[configuration reference](configuration-reference.md).

## The dashboard is up but no comics appear

The honest chain to check, in order:

1. **No LLM configured.** Startup says
   `LLM_URL is not set; skipping the LLM call`, and the first-comic cycle
   logs `Error executing immediate comic cycle: the LLM did not invent a new
   arc; the next cycle retries`. Set `LLM_URL` (Settings → Story LLM → Test)
   to any OpenAI-compatible endpoint. Empty means mining — and therefore
   comics — stay off.
2. **LLM set but unreachable.** The probe message is
   `Could not reach the endpoint.` with a `ConnectError` in the details.
   Check the URL (many llama.cpp-style servers serve at `/v1` — e.g.
   `http://host:8080/v1`), and that the endpoint is up and reachable from
   the *server's* network position (container, VPN…).
3. **Image rendering.** Without a render backend the pipeline dies later,
   after mining. `POST /api/config/test/image` (or Settings → Test
   connection) distinguishes:
   - `torch/diffusers not installed — install the comack-server-ai extra, or
     set FLUX_API_URL to render on an external image API.` — light install;
     either install the heavy stack or point `FLUX_API_URL` elsewhere.
   - `torch/diffusers installed, but the cuda device is not available.` —
     the wheel exists but no usable CUDA: check the driver, or set
     `FLUX_DEVICE=cpu`/`auto`.
4. **Waiting on cadence.** With mining healthy, a comic needs
   `DEFAULT_LINES_WANTED` scrambled lines, and never more often than
   `COMIC_MIN_MINUTES`. `/api/status` shows `lines_collected` vs
   `lines_wanted` — "Generate Comic Now" bypasses the wait.

## Whisper model downloads

The transcription model is fetched from Hugging Face on first use (once
`faster-whisper` is installed) into `~/.cache/huggingface/hub`. Approximate
CT2 download sizes — a one-time fetch per size:

| `WHISPER_MODEL_SIZE` | Approx. download |
|---|---|
| `tiny.en` | ~75 MB |
| `base.en` | ~145 MB |
| `small.en` | ~485 MB |
| `medium.en` | ~1.5 GB |

(Sizes are approximate; they are not verified in this sandbox — the light
install deliberately never downloads them. The **Test connection** probe for
Whisper reports whether the configured size is already cached, without
triggering a download.)

Slow or failing pulls: the download happens at model load inside the server
process; a flaky connection shows as a long startup or a fallback warning.
Once cached, set `HF_HUB_OFFLINE=1` (unit or environment) to skip the
per-load revision check — unset it when you change `WHISPER_MODEL_SIZE` or
the new model can't be fetched.

## VRAM / OOM (local rendering)

- **OOM at render time.** `FLUX_MIN_FREE_VRAM_GB` (default 10.5) pauses
  rendering while free VRAM is below the floor, waiting up to
  `FLUX_VRAM_WAIT_SECONDS` (180 s) per panel before giving up. If your LLM
  shares the GPU, either lower the floor (`FLUX_MIN_FREE_VRAM_GB`) or point
  rendering elsewhere (`FLUX_API_URL`).
- **The image probe reports low/zero VRAM but the GPU exists** — that is the
  guard working; the render waits instead of racing the LLM.
- **Repeated "give up on a panel"** — raise `FLUX_VRAM_WAIT_SECONDS`, lower
  `FLUX_INFERENCE_STEPS` (SD-Turbo runs at 1), switch
  `FLUX_MODEL_ID=stabilityai/sd-turbo` (the smallest), or use the
  [cpu-only preset](../presets/cpu-only.env) which also stretches the comic
  cadence.
- **Machine without any GPU**: use `presets/external-api.env` (render on a
  remote OpenAI-compatible image API) or `presets/cpu-only.env`.

## Probe failures and what they mean

`POST /api/config/test/{llm,image,whisper}` are read-only: they never change
settings and never download models; every probe answers 200 with
`{"ok": false, "message": …}` on failure. Exact messages (verified on a
light install):

| Message | Meaning | Fix |
|---|---|---|
| `No LLM endpoint configured — story mining is disabled until one is set.` | `LLM_URL` is empty | Set `LLM_URL` |
| `Could not reach the endpoint.` (+ `ConnectError` in details) | The URL didn't answer in 5 s | Host/port/path, endpoint up? |
| `Endpoint answered, but /v1/models returned HTTP <n>.` | Reachable, but not an OpenAI-shaped API (or auth needed) | Check the URL includes `/v1`, API keys, server type |
| `Reachable; model '<name>' is available.` / `Model not in the endpoint's list…` (note, not failure) | Model-name check against the endpoint's model list | llama.cpp-style servers ignore the name; verify it yourself |
| `torch/diffusers not installed — install the comack-server-ai extra, or set FLUX_API_URL…` | Light install, local render | Install `[comack-server-ai]` or set `FLUX_API_URL` |
| `torch/diffusers installed, but the <device> device is not available.` | Heavy stack present, device missing | Driver; or `FLUX_DEVICE=cpu` |
| `faster-whisper not installed — transcription falls back to the offline mock. Install the comack-server-ai extra for real transcription.` | Light install | Audio still streams; transcription is a mock until the extra is installed |
| `faster-whisper installed; '<size>' is cached locally…` / `…will download from Hugging Face on first use.` | Install state and cache check | — |

## Device not appearing

The device connects *out* to the server; check the server side first:

1. **Server log.** A successful device stream logs
   `reTerminal E1001 audio streamer connected.` on `/ws/audio`. Absent line =
   the device isn't reaching the server (wrong `HOST:PORT`, different
   network, Tailscale down).
2. **Event feed.** Display refreshes ride `/ws/events`; the simulator and
   firmware reconnect every 2 s on failure, so a transient server restart
   self-heals.
3. **Server reachable?** `curl http://<server>:8000/api/status` from the
   device's network position. With `WEB_TOKEN` set, `/ws/audio` and the
   comic-read paths stay tokenless on purpose (see
   [self-hosting](self-hosting.md#access-token-web_token)) — the device never
   sends a token, so a 401 on those paths is a bug, not a config problem.
4. **Blank e-paper, server fine.** The panel stays white until a comic
   completes — with no LLM configured nothing will ever render (see the top
   of this page).
5. **Firmware-side causes** (flashing, WiFi credentials, button wiring) are
   covered in [firmware/README.md](../firmware/README.md).
