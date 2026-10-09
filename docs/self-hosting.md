# Self-hosting

Running Comack on a machine you own, for longer than a terminal session —
Docker Compose, systemd, and the optional `WEB_TOKEN` access control.

Three deployment shapes; pick by where rendering happens:

| Shape | Fits | Notes |
|---|---|---|
| [Docker Compose](#docker-compose) | "Just run it" on a Linux box | Image includes the full AI stack; settings are set in the compose file |
| [systemd + venv](#systemd-venv-the-comack-server-profile) | The comack-server profile: local GPU, models cached, boots with the system | What `deploy_comack-server.sh` sets up |
| Light server + external APIs | A small box (or laptop) with no GPU; rendering and mining happen elsewhere | `presets/external-api.env`, uvicorn in a venv or tmux |

Verification status for this page, stated up front: `docker compose config`
was **executed** against this repo's compose file (Compose v2.39.4) — the
container **build and run were not** (no Docker daemon in the sandbox where
this was verified). `comack.service` and `deploy_comack-server.sh` were
**inspected**, not run against a real host. The `WEB_TOKEN` behavior was
**executed and verified** against a running server.

## Docker compose

```sh
git clone https://github.com/locriani/comack && cd comack
docker compose up -d --build
# Dashboard: http://localhost:8000/
```

What you get (from `docker-compose.yml`, validated with
`docker compose config`):

- **`build: .`** — [Dockerfile](../Dockerfile) builds `python:3.11-slim`,
  installs `requirements.txt`, which carries the **full AI stack**
  (torch, diffusers, faster-whisper) on top of the light core. First comic
  downloads the configured image model (SD-Turbo by default).
- **`network_mode: host`** — the server binds the host's ports directly; the
  device streams to the same address the dashboard uses. There is no separate
  port mapping to edit.
- **`./data` bind-mounted to `/app/data`** — comics and the database survive
  rebuilds and live where you can back them up.
- **`restart: unless-stopped`**.

### Settings with compose — important

`docker-compose.yml` hardcodes its settings in the service's `environment:`
block (`LLM_URL`, cadence bounds, `POS_SCRAMBLE_RATE`, …). Compose does **not
read the repo's `.env`** for these — a `.env` file you create for a venv
install has no effect on the container, because the compose file contains no
`${VAR}` interpolation. To change container settings:

- edit the `environment:` block in `docker-compose.yml`, then
  `docker compose up -d` to recreate, **or**
- add explicit interpolation to the compose file
  (`- LLM_URL=${LLM_URL:?set in .env}`) if you prefer `.env`-driven
  deployments.

A related consequence: `PATCH /api/config` and the settings page persist
their changes to `.env` at the app root — inside the container that is
`/app/.env`, which is **not** on the bind-mounted volume and does not survive
container recreation. Any container setting you care about belongs in the
compose `environment:` block (where, as a real environment variable, it also
outranks any `.env`).

The compose file validated cleanly with:

```sh
docker compose config -q   # exit 0 — syntax + schema OK (verified)
```

## systemd (venv) — the comack-server profile

For a GPU machine with models already cached, run the server as a system
service. [`comack.service`](../comack.service) ships in the repo root; before
installing, edit:

1. `User=` — the user that owns the checkout (and access to the GPU).
2. `WorkingDirectory=` and `ExecStart=` — where the repo lives; ExecStart
   points at the venv's uvicorn (`/home/your-user/comack/.venv/bin/uvicorn`).
3. `Environment=LLM_URL=` — your OpenAI-compatible endpoint (plain IP, LAN
   hostname, or Tailscale hostname), e.g. `http://your-llm-server:80/`.
4. `Environment=FLUX_MODEL_ID=` — the unit defaults to
   `black-forest-labs/FLUX.1-schnell` (GGUF, ~10 GB VRAM headroom); use
   `stabilityai/sd-turbo` on smaller GPUs.
5. `Environment=HF_HUB_OFFLINE=1` — the unit ships with it because models are
   expected to be cached already; **unset it once** when a new model needs
   downloading.

Then:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt    # full stack incl. torch/diffusers
sudo cp comack.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now comack
journalctl -u comack -f                      # watch the startup log
```

Real environment variables in the unit **win over `.env`**: if you later edit
a setting in the dashboard or `.env` and it "doesn't take", the unit is
pinning it. Edit the unit instead (then `daemon-reload` + `restart`).

## Deploying to a remote host

[`deploy_comack-server.sh`](../deploy_comack-server.sh) rsyncs the repo to a host
you control — a LAN name, IP, or Tailscale hostname:

```sh
./deploy_comack-server.sh comack@myhost.tail1234.ts.net /home/comack/comack
```

It excludes `.env` (the target's own settings are authoritative there),
`.venv`, `.git`, `firmware/`, and `data/` (the target builds its own history).
It prints the follow-up commands (compose, or the systemd path above); it
does not install anything on the target by itself.

## Access token (WEB_TOKEN)

Optional. With `WEB_TOKEN` empty (the default), everything is open — the
appliance assumes a single-user network such as a tailnet. When set, the
split (verified against a running server; exact rules in
[`server/auth.py`](../server/auth.py)):

**Requires the token** — everything under `/api` plus the `/ws/events` feed.
Browsers send `Authorization: Bearer <token>` via fetch; the event-feed
WebSocket, which cannot set headers, appends `?token=` (accepted only on that
path, so tokens don't land in access logs for ordinary requests).

**Always open, on purpose**:

- `/` and `/reader` — the HTML shells. They are the same static code
  `/static/` serves; a browser cannot attach Authorization headers to
  top-level navigation, so gating the shells would 401 the page itself and
  the in-page token challenge could never boot.
- `/static/**` — code only.
- `/ws/audio` — the microphone stream; the firmware has no header capability
  and the appliance must keep working unattended.
- Comic reads the device and `<img>` tags need:
  `/api/comics/current` (+ `.png`/`.bin`), `/api/comics/<n>/png|bin`, and the
  per-panel `/api/comics/<n>/(color|panelN).png`.

Token comparisons are constant-time.

### Setting it

Any of the usual ways — it is a normal hot-applied setting:

```sh
curl -X PATCH localhost:8000/api/config \
  -H 'Content-Type: application/json' -d '{"WEB_TOKEN": "s3cret"}'
# → {"results": {"WEB_TOKEN": "applied"}}
```

(or the settings page's WEB_TOKEN field, or a line in `.env`, or a real
environment variable for compose/systemd — which then outranks `.env`).

What you see in a browser: the dashboard shell still loads (it is just code),
its API calls get 401s, and the page prompts once for the token, remembering
it in the browser's localStorage.

Verified behavior when set (all executed):

| Request | Result |
|---|---|
| `GET /api/status` no token | 401 |
| `GET /api/status` wrong token | 401 |
| `GET /api/status` right Bearer token | 200 |
| `GET /` and `GET /reader` no token | 200 (shells) |
| `GET /api/comics/current` no token | not-401 (open device path; 404 only because no comic existed yet) |
| `GET /api/comics/history` no token | 401 (gated) |
| `PATCH /api/config` no token | 401 — **unsetting the token requires the token**: send the same Bearer header with `{"WEB_TOKEN": ""}` |

Choose a long random value (`openssl rand -hex 32`). Combining with a reverse
proxy or Tailscale is encouraged — the token is one layer, not the only one.
