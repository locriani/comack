#!/usr/bin/env bash
# Comack server setup — one idempotent script for the venv/systemd path.
#
# Each step skips itself when already done, so re-running is always safe:
#   1. checks for Python 3.11+
#   2. creates .venv
#   3. installs the light core (--ai adds the comack-server-ai extra)
#   4. seeds .env from .env.example — never overwrites an existing .env
#   5. renders comack.service with the real user and paths into
#      comack.service.rendered (installing it when systemd is present)
#   6. with --with-tailscale: installs tailscaled (official installer) and
#      joins the tailnet — key from TS_AUTH_KEY, a prompt, or the
#      interactive login URL
#
# Tracked files are never modified: the rendered unit is a new file and .env
# is only created when absent. When this finishes, open the dashboard and run
# the onboarding wizard — docs/installation.md has the details.

# The script uses bash-only features (EUID, BASH_SOURCE, arrays-free but
# still ${BASH_SOURCE[0]}); refuse other shells instead of failing oddly.
if [ -z "${BASH_VERSION:-}" ]; then
    echo "error: run this script with bash: ./scripts/setup.sh" >&2
    exit 1
fi

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/setup.sh [--ai] [--with-tailscale]

Idempotent server setup for Comack (venv + optional systemd unit).

  --ai               also install the comack-server-ai extra (local Whisper and
                     FLUX rendering; several GB of wheels)
  --with-tailscale   install tailscaled (official installer) and join the
                     tailnet. Auth key: TS_AUTH_KEY env var, a prompt, or
                     the interactive login URL.
  -h, --help         show this help

Every step skips itself when already done; re-running is always safe.
EOF
}

SETUP_AI=0
SETUP_TAILSCALE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --ai) SETUP_AI=1 ;;
        --with-tailscale) SETUP_TAILSCALE=1 ;;
        -h | --help) usage; exit 0 ;;
        *)
            echo "error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ "$EUID" -eq 0 ]; then
    echo "note: running as root — the venv and rendered files will be owned by root."
    echo "      Prefer running as the user that owns this checkout."
fi

# Run a command as root when possible: directly as root, via sudo otherwise.
# A 127 return means "no way to get root" — callers print a manual fallback.
as_root() {
    if [ "$EUID" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        return 127
    fi
}

# ── 1. Python ────────────────────────────────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found — install Python 3.11+ and re-run." >&2
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "error: Python 3.11+ required, found $(python3 --version)" >&2
    exit 1
fi

# ── 2. Virtual environment ───────────────────────────────────────────────────
if [ -x ".venv/bin/python" ]; then
    echo "== .venv already exists — skipping creation"
elif [ -d ".venv" ]; then
    echo "error: .venv exists but has no bin/python (a partial venv?)." >&2
    echo "       Remove it and re-run: rm -rf .venv" >&2
    exit 1
elif ! python3 -m venv .venv; then
    echo "error: could not create the virtual environment." >&2
    echo "       (Debian/Ubuntu: sudo apt install python3-venv)" >&2
    exit 1
else
    echo "== Created virtual environment (.venv)"
fi
VENV_PY=".venv/bin/python"

# ── 3. Dependencies ──────────────────────────────────────────────────────────
# Installed-package metadata is the "already done" marker: offline and fast.
if "$VENV_PY" -m pip show comack >/dev/null 2>&1; then
    echo "== comack already installed in .venv — skipping (light core)"
else
    echo "== Installing the light core (pip install -e .)"
    "$VENV_PY" -m pip install -e .
fi
if [ "$SETUP_AI" -eq 1 ]; then
    # faster-whisper is distinctive to the comack-server-ai extra; pip show is
    # an offline proxy for "the heavy group is in".
    if "$VENV_PY" -m pip show faster-whisper >/dev/null 2>&1; then
        echo "== comack-server-ai extra already installed — skipping"
    else
        echo "== Installing the comack-server-ai extra (torch, diffusers, faster-whisper)"
        "$VENV_PY" -m pip install -e '.[comack-server-ai]'
    fi
fi

# ── 4. Settings ──────────────────────────────────────────────────────────────
if [ -f ".env" ]; then
    echo "== .env already exists — leaving it untouched"
else
    echo "== Seeding .env from .env.example"
    cp .env.example .env
fi

# ── 5. systemd unit ──────────────────────────────────────────────────────────
# Renders comack.service with the real user and paths. The LLM_URL pin is
# replaced with a note rather than a value: real environment variables in the
# unit outrank .env, so pinning it would silently defeat wizard/dashboard
# edits. The FLUX_MODEL_ID / SNEAKY_SILENCE_SECONDS / HF_HUB_OFFLINE pins
# stay — they are the comack-server profile defaults.
RUN_USER="$(id -un)"
RENDERED="comack.service.rendered"
sed -E \
    -e "s|^# Run as a dedicated.*|# Rendered by scripts/setup.sh from comack.service: real user and paths.\\n# Edit comack.service and re-run setup.sh to change them.|" \
    -e "s|^User=.*|User=${RUN_USER}|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=${ROOT}|" \
    -e "s|^ExecStart=.*|ExecStart=${ROOT}/.venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8000|" \
    -e "s|^# LLM_URL:.*|# LLM_URL is read from the repo-root .env (like every other setting), so|" \
    -e "s|^#   http://<your-llm-server>:80/.*|# edits made in the dashboard's settings page take effect. The pins below|" \
    -e "s|^Environment=LLM_URL=.*|# are the comack-server profile defaults; remove them to let .env win.|" \
    comack.service > "$RENDERED"

if grep -Eq 'your-user|your-llm-server' "$RENDERED"; then
    echo "error: rendered unit still contains placeholders — refusing to install:" >&2
    grep -En 'your-user|your-llm-server' "$RENDERED" >&2
    exit 1
fi
echo "== Rendered systemd unit: $ROOT/$RENDERED (user: ${RUN_USER}, workdir: $ROOT)"

UNIT_DST="/etc/systemd/system/comack.service"
if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    if [ -f "$UNIT_DST" ] && cmp -s "$RENDERED" "$UNIT_DST"; then
        echo "== systemd unit already installed and current — skipping"
    elif as_root install -m 644 "$RENDERED" "$UNIT_DST" && as_root systemctl daemon-reload; then
        echo "== Installed $UNIT_DST"
        echo "   Start it with: sudo systemctl enable --now comack"
    else
        echo "   Could not install the unit (no passwordless root?) — copy it manually:"
        echo "     sudo cp $ROOT/$RENDERED $UNIT_DST && sudo systemctl daemon-reload"
    fi
else
    echo "== systemd not detected here — to install the unit on a systemd host:"
    echo "     sudo cp $ROOT/$RENDERED $UNIT_DST && sudo systemctl daemon-reload"
fi

# ── 6. Tailscale (--with-tailscale) ──────────────────────────────────────────
setup_tailscale() {
    if command -v tailscale >/dev/null 2>&1; then
        echo "== tailscale already installed — skipping install"
    else
        echo "== Installing Tailscale (official installer: tailscale.com/install.sh)"
        if ! curl -fsSL https://tailscale.com/install.sh | sh; then
            echo "warning: the Tailscale installer failed." >&2
            echo "  Manual fallback: https://tailscale.com/download — or install the" >&2
            echo "  'tailscale' package with your distro's package manager, then re-run." >&2
            return 1
        fi
    fi

    # Joined already? BackendState is "Running" once logged in.
    ts_state="$(tailscale status --json 2>/dev/null | grep -o '"BackendState"[^,}]*' | head -n1 | cut -d '"' -f4 || true)"
    if [ "$ts_state" = "Running" ]; then
        echo "== tailscale already connected — skipping login"
        return 0
    fi

    local ts_key=""
    if [ -n "${TS_AUTH_KEY:-}" ]; then
        ts_key="$TS_AUTH_KEY"
        echo "== Joining the tailnet with the key from TS_AUTH_KEY"
    else
        echo "== No TS_AUTH_KEY in the environment."
        read -r -s -p "Tailscale auth key (empty = interactive login URL): " ts_key
        echo
    fi
    if [ -n "$ts_key" ]; then
        # The key is briefly visible in this box's process list — the same
        # single-user appliance tradeoff the tokenless device paths make.
        as_root tailscale up --auth-key="$ts_key"
    else
        echo "== Running 'tailscale up' — open the printed URL in a browser to log in"
        as_root tailscale up
    fi
}
if [ "$SETUP_TAILSCALE" -eq 1 ]; then
    setup_tailscale
fi

# ── Next steps ───────────────────────────────────────────────────────────────
DASH_PORT="8000"
if [ -f ".env" ]; then
    env_port="$(grep -E '^PORT=' .env | tail -n1 | cut -d '=' -f2 | tr -d '[:space:]' || true)"
    if [ -n "$env_port" ]; then
        DASH_PORT="$env_port"
    fi
fi

echo
echo "Setup complete."
echo "Next steps:"
echo "  1. Start the server:  sudo systemctl enable --now comack   (systemd)"
echo "                        — or —  ./run_server.sh   (venv, foreground)"
echo "                        — or —  docker compose up -d --build"
echo "  2. Open the dashboard: http://localhost:${DASH_PORT}/"
echo "  3. Run the onboarding wizard (Story LLM, Whisper, image backend, consent)."
