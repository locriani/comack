# Self-hosting

Running Comack on a machine you own, for longer than a terminal session —
Docker Compose, systemd, Tailscale, and the optional `WEB_TOKEN` access
control.

Three deployment shapes; pick by where rendering happens:

| Shape | Fits | Notes |
|---|---|---|
| [Docker Compose](#docker-compose) | "Just run it" on a Linux box | Image includes the full AI stack; settings live in `.env` and survive recreation |
| [systemd + venv](#systemd-venv--the-local-gpu-profile) | The local-GPU profile: local GPU, models cached, boots with the system | What `deploy_server.sh` sets up |
| Light server + external APIs | A small box (or laptop) with no GPU; rendering and mining happen elsewhere | `presets/external-api.env`, uvicorn in a venv or tmux |

Verification status for this page, stated up front: `docker compose config`
was **executed** against this repo's compose file (Compose v5.6.0) — the
container **build and run were not** (no Docker daemon in the sandbox where
this was verified). `scripts/setup.sh` was **executed end-to-end** on a clean
checkout (light core; the second run verified as a no-op, an edited `.env`
left untouched, and the rendered `comack.service.rendered` free of
placeholders), `bash -n` and shellcheck 0.10.0 clean. The `--with-tailscale`
path was **partially exercised**: its already-installed / already-connected
skips and its installer-failure fallback ran in that sandbox; a real install
and `tailscale up` did not (no tailnet there). `comack.service` and
`deploy_server.sh` were **inspected**, not run against a real host. The
`WEB_TOKEN` behavior was **executed and verified** against a running server.

## Docker compose

```sh
git clone https://github.com/locriani/comack && cd comack
cp .env.example .env     # settings live here — the container mounts it
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
- **`./.env` bind-mounted to `/app/.env`** — the container reads its settings
  from the same `.env` a venv install uses, and dashboard edits persist into
  it ([details below](#settings-with-compose)). Create it before the first
  `up` (the `cp` above): if the host file is missing, Docker creates a
  *directory* at that path and the server cannot read its settings.
- **`./data` bind-mounted to `/app/data`** — comics and the database survive
  rebuilds and live where you can back them up.
- **`restart: unless-stopped`**.

### Settings with compose

The container reads its settings from the repo-root `.env` (bind-mounted to
`/app/.env`) — the same file a venv install uses, seeded from
[`.env.example`](../.env.example). `PATCH /api/config` and the settings page
persist their changes to that file, so **edits made in the dashboard survive
container recreation**. To change a setting: edit `.env` (or use the
dashboard) and `docker compose up -d` — hot-applied settings take effect
immediately, the restart-required ones on the recreate. The compose file sets
no environment variables of its own, so `.env` is authoritative (a real
environment variable would still outrank it — see
[how configuration works](configuration-reference.md#how-configuration-works)).

The compose file validated cleanly with:

```sh
docker compose config -q   # exit 0 — syntax + schema OK (verified)
```

## systemd (venv) — the local-GPU profile

For a GPU machine with models already cached, run the server as a system
service. [`scripts/setup.sh`](../scripts/setup.sh) does the whole path: it
creates the venv, installs the light core (`--ai` adds the full
torch/diffusers/faster-whisper stack of `requirements.txt`), seeds `.env`,
and renders [`comack.service`](../comack.service) with your user and real
paths into `comack.service.rendered` — installing it when systemd is
present, printing the copy commands when not:

```sh
./scripts/setup.sh            # add --ai for the local inference stack
sudo systemctl enable --now comack
journalctl -u comack -f       # watch the startup log
```

The rendered unit pins the local-GPU-profile defaults
(`FLUX_MODEL_ID=black-forest-labs/FLUX.1-schnell` — the GGUF model with
~10 GB VRAM headroom; use `stabilityai/sd-turbo` on smaller GPUs — plus
`HF_HUB_OFFLINE=1` because models are expected to be cached already, and
`SNEAKY_SILENCE_SECONDS=0`). `LLM_URL` and every other setting are read
from the repo-root `.env`: the unit deliberately does not pin them, so
edits made in the dashboard's settings page take effect. (Unset
`HF_HUB_OFFLINE` once when a new model needs downloading.)

Real environment variables in the unit **win over `.env`**: the pins above
are the only ones. If you later edit a pinned setting in the dashboard and
it "doesn't take", that pin is why — edit the unit (edit `comack.service`,
re-run `scripts/setup.sh`, re-install the rendered file), then
`daemon-reload` + `restart`.

## Deploying to a remote host

[`deploy_server.sh`](../deploy_server.sh) rsyncs the repo to a host
you control — a LAN name, IP, or Tailscale hostname:

```sh
./deploy_server.sh comack@myhost.tail1234.ts.net /home/comack/comack
```

It excludes `.env` (the target's own settings are authoritative there),
`.venv`, `.git`, `firmware/`, and `data/` (the target builds its own history).
It prints the follow-up commands (compose, or the systemd path above); it
does not install anything on the target by itself.

## Tailscale

The device is a native tailnet node (via its vendored MicroLink client) and
resolves the server by its Tailscale **MagicDNS** name — the compile-time
default is `localhost`, which only suits the desktop simulator; set your
server's hostname in `firmware/crates/comack-core/src/config.rs` before
flashing an ESP target. The rest of this page used to assume Tailscale was
already set up; this section closes that gap.

The one-script path — installs tailscaled with the official installer, then
joins (key from the `TS_AUTH_KEY` environment variable, a prompt, or the
interactive login URL; whichever step is already done is skipped):

```sh
TS_AUTH_KEY=tskey-auth-… ./scripts/setup.sh --with-tailscale
```

The same two steps by hand:

```sh
curl -fsSL https://tailscale.com/install.sh | sh   # official installer
sudo tailscale up                                  # prints a login URL, or:
sudo tailscale up --auth-key=tskey-auth-… --hostname=my-server
```

- **Auth keys** are pre-generated at
  [login.tailscale.com](https://login.tailscale.com) (`tskey-auth-…`);
  `--auth-key=` lets a headless box join without a browser.
- **Name the server's node whatever you like** (`--hostname=my-server`) —
  then set that name as `SERVER.host` in
  `firmware/crates/comack-core/src/config.rs` and flash the device with it:
  the value is compiled in (the default, `localhost`, only suits the desktop
  simulator; a runtime-stored config overrides it once the on-device portal
  ships). A server that should stay connected indefinitely is also a
  candidate for disabling key expiry in the admin console — a security
  tradeoff, so only for machines you trust.
- **WEB_TOKEN and the tailnet:** with `WEB_TOKEN` empty, browser-facing
  APIs are open — defensible because the tailnet is a private, single-user
  network, and the device's paths must stay tokenless regardless (see the
  next section). Set `WEB_TOKEN` when you want the browser APIs locked even
  inside the tailnet; it is one layer, not the only one.

Verification: the install and join steps follow Tailscale's documented
Linux install ([tailscale.com/kb/1031/install-linux](https://tailscale.com/kb/1031/install-linux))
and were **inspected, not executed** against a real tailnet (none in the
verification sandbox); `--with-tailscale`'s skip logic and its
installer-failure fallback were exercised in that sandbox.

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
proxy or Tailscale is encouraged — see [Tailscale](#tailscale) above; the
token is one layer, not the only one.
