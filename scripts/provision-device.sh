#!/usr/bin/env bash
# Provision a Comack device over USB: render the compile-time fallback
# (firmware/esp/src/secrets.rs) from the checked-in example, build the
# ESP32-S3 firmware, and flash it.
#
# The on-device setup portal is the primary provisioning path (it changes
# networks without a cable); this script is the assisted first flash. It
# collects the same fields the portal does and writes the ones the
# compile-time fallback can carry (Wi-Fi + Tailscale key; see the note it
# prints about server address and device name).
#
# Re-running with the same values after a successful flash is a no-op;
# pass --force to build and flash again.
set -euo pipefail

# Byte-accurate ${#...} length checks (SSIDs and passphrases are byte-limited).
export LC_ALL=C

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ESP_DIR="$REPO_ROOT/firmware/esp"
SECRETS="$ESP_DIR/src/secrets.rs"
SECRETS_EXAMPLE="$ESP_DIR/src/secrets.rs.example"
STATE_FILE="$ESP_DIR/.provision-state"
ELF="$ESP_DIR/target/xtensa-esp32s3-espidf/release/comack-firmware"

# Compile-time constants the fallback cannot carry (firmware/crates/
# comack-core/src/config.rs). Used for the default answers and the notice.
DEFAULT_SERVER="localhost:8000"
DEFAULT_NAME="comack-display"

CTRL_RE='[[:cntrl:]]'
HOST_RE='^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$'
NAME_RE='^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$'

usage() {
  cat <<'EOF'
Provision a Comack device over USB: render the compile-time fallback
(firmware/esp/src/secrets.rs), build the ESP32-S3 firmware, and flash it.

The on-device setup portal is the primary provisioning path (join its
comack-setup network from a phone; no cable after the first flash). This
script is the assisted first flash: same fields, rendered into the
compile-time fallback and flashed over USB.

Usage: scripts/provision-device.sh [options]

  --ssid SSID           Wi-Fi network to compile into the fallback (max 32 chars)
  --password PW         Wi-Fi password (max 64 chars; hidden prompt if omitted)
  --server HOST[:PORT]  server address (hostname or IPv4; recorded and checked
                        against the compile-time default localhost:8000)
  --name NAME           device name (recorded and checked against the
                        compile-time default comack-display)
  --tskey KEY           Tailscale auth key (optional; empty = LAN-only.
                        secrets.rs is scrubbed empty after a successful flash)
  --serial-port DEV     serial device for espflash (default: auto-detect)
  --monitor             open the serial monitor after flashing (or instead of
                        flashing when there is nothing to do)
  --e1001               force the monochrome E1001 build (skip auto-detect)
  --e1002               force the colour E1002 build (skip auto-detect)
                        When neither is passed, scripts/detect-reterminal-board.sh
                        picks the board from cache / boot log / flash marker.
  --no-secrets          leave secrets.rs untouched (it must already exist)
  --force-secrets      overwrite an existing, differing secrets.rs
  --no-flash            render + build only; nothing is flashed and no
                        provisioning state is recorded
  --force               build and flash even when nothing changed
  --yes                 never prompt; fail when a required value is missing
  --help                show this help

Anything not passed as a flag is prompted for interactively. Re-running with
the same values after a successful flash is a no-op.
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

warn() {
  echo "warning: $*" >&2
}

SSID=""
PASSWORD=""
SERVER=""
NAME=""
TSKEY=""
SERIAL_PORT=""
MONITOR=0
# -1 = auto-detect; 0 = E1001; 1 = E1002
E1002=-1
NO_SECRETS=0
FORCE_SECRETS=0
NO_FLASH=0
FORCE=0
ASSUME_YES=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --ssid)          [ "$#" -ge 2 ] || die "--ssid needs a value"; SSID=$2; shift 2 ;;
    --password)      [ "$#" -ge 2 ] || die "--password needs a value"; PASSWORD=$2; shift 2 ;;
    --server)        [ "$#" -ge 2 ] || die "--server needs a value"; SERVER=$2; shift 2 ;;
    --name)          [ "$#" -ge 2 ] || die "--name needs a value"; NAME=$2; shift 2 ;;
    --tskey)         [ "$#" -ge 2 ] || die "--tskey needs a value"; TSKEY=$2; shift 2 ;;
    --serial-port)   [ "$#" -ge 2 ] || die "--serial-port needs a value"; SERIAL_PORT=$2; shift 2 ;;
    --monitor)       MONITOR=1; shift ;;
    --e1001)         E1002=0; shift ;;
    --e1002)         E1002=1; shift ;;
    --no-secrets)   NO_SECRETS=1; shift ;;
    --force-secrets) FORCE_SECRETS=1; shift ;;
    --no-flash)      NO_FLASH=1; shift ;;
    --force)         FORCE=1; shift ;;
    --yes)           ASSUME_YES=1; shift ;;
    --help|-h)       usage; exit 0 ;;
    *)               die "unknown argument: $1 (see --help)" ;;
  esac
done

DETECT_SCRIPT="$REPO_ROOT/scripts/detect-reterminal-board.sh"

resolve_board() {
  # Sets E1002 to 0 or 1. Auto-detect when neither --e1001 nor --e1002 was passed.
  if [ "$E1002" -ne -1 ]; then
    return 0
  fi
  local detect_args=() board
  if [ -n "$SERIAL_PORT" ]; then
    detect_args+=(--serial-port "$SERIAL_PORT")
  fi
  if [ ! -x "$DETECT_SCRIPT" ]; then
    die "board auto-detect script missing or not executable: $DETECT_SCRIPT"
  fi
  echo "Auto-detecting reTerminal board (E1001 vs E1002)…"
  if ! board="$("$DETECT_SCRIPT" "${detect_args[@]}")"; then
    if [ "$INTERACTIVE" -eq 1 ]; then
      local reply
      reply="$(prompt_visible "Board model (e1001 or e1002)" "")"
      case "$reply" in
        e1001|E1001) board=e1001 ;;
        e1002|E1002) board=e1002 ;;
        *) die "board must be e1001 or e1002 (got: $reply)" ;;
      esac
    else
      die "board auto-detect failed — pass --e1001 or --e1002 (see scripts/detect-reterminal-board.sh)"
    fi
  fi
  case "$board" in
    e1001) E1002=0 ;;
    e1002) E1002=1 ;;
    *) die "detect-reterminal-board.sh returned an unexpected value: $board" ;;
  esac
  echo "Using board: $board"
}

# Prompted answers only when stdin is a terminal; --yes turns prompts off.
INTERACTIVE=0
if [ -t 0 ] && [ "$ASSUME_YES" -eq 0 ]; then
  INTERACTIVE=1
fi

prompt_visible() { # prompt_visible <label> <default>: prints the answer
  local label=$1 default=$2 reply=""
  if [ -n "$default" ]; then
    printf '%s [%s]: ' "$label" "$default" >&2
  else
    printf '%s: ' "$label" >&2
  fi
  if ! read -r reply; then
    die "no answer (input closed) — pass the value as a flag instead"
  fi
  printf '%s' "${reply:-$default}"
}

prompt_hidden() { # prompt_hidden <label>: prints the answer, input hidden
  local label=$1 reply=""
  printf '%s: ' "$label" >&2
  if ! read -r -s reply; then
    die "no answer (input closed) — pass the value as a flag instead"
  fi
  printf '\n' >&2
  printf '%s' "$reply"
}

# Control characters (including newlines) never belong in any field and would
# corrupt the rendered Rust literal.
validate_no_ctrl() {
  if [[ $1 =~ $CTRL_RE ]]; then
    die "$2 must not contain control characters"
  fi
}

validate_ssid() {
  if [ -z "$1" ]; then
    die "SSID must not be empty"
  fi
  validate_no_ctrl "$1" "SSID"
  if [ "${#1}" -gt 32 ]; then
    die "SSID is longer than 32 characters (802.11 limit)"
  fi
}

validate_password() {
  if [ -z "$1" ]; then
    die "Wi-Fi password must not be empty"
  fi
  validate_no_ctrl "$1" "Wi-Fi password"
  if [ "${#1}" -gt 64 ]; then
    die "Wi-Fi password is longer than 64 characters (WPA2 limit)"
  fi
  if [ "${#1}" -lt 8 ]; then
    warn "Wi-Fi password is shorter than 8 characters (WPA2 minimum)"
  fi
}

validate_server() { # host or host:port; the firmware resolves IPv4 only
  local value=$1 host port
  if [ -z "$value" ]; then
    die "server address must not be empty"
  fi
  validate_no_ctrl "$value" "server address"
  if [[ $value =~ [[:space:]] ]]; then
    die "server address must not contain whitespace: '$value'"
  fi
  if [[ $value == *:*:* ]]; then
    die "server address must be a hostname or IPv4 with an optional :port (IPv6 is not supported: the firmware resolves IPv4 only)"
  fi
  host=${value%%:*}
  if [[ $value == *:* ]]; then
    port=${value#*:}
    if [[ ! $port =~ ^[0-9]+$ ]] || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
      die "server port must be 1-65535: '$value'"
    fi
  fi
  if [[ ! $host =~ $HOST_RE ]] || [ "${#host}" -gt 253 ]; then
    die "server host must be a hostname or IPv4 address: '$host'"
  fi
}

validate_name() { # a DNS label: letters, digits, hyphens
  local value=$1
  if [ -z "$value" ]; then
    die "device name must not be empty"
  fi
  validate_no_ctrl "$value" "device name"
  if [ "${#value}" -gt 31 ]; then
    die "device name is longer than 31 characters"
  fi
  if [[ ! $value =~ $NAME_RE ]]; then
    die "device name must contain only letters, digits and hyphens: '$value'"
  fi
}

validate_tskey() {
  local value=$1
  if [ -z "$value" ]; then
    return 0 # optional: the example's placeholder is kept
  fi
  validate_no_ctrl "$value" "Tailscale auth key"
  if [[ $value != tskey-auth-* ]]; then
    warn "Tailscale auth key does not start with 'tskey-auth-' — check it"
  fi
  if [ "${#value}" -gt 79 ]; then
    warn "Tailscale auth key is longer than 79 characters — check it"
  fi
}

# Replace every occurrence of a literal needle. Backslash-safe by design:
# the replacement is appended by concatenation, never re-scanned, because
# ${var//pat/repl} would process backslashes in the replacement.
replace_all() { # replace_all <haystack> <needle> <replacement>: prints result
  local haystack=$1 needle=$2 repl=$3 out=""
  local rest=$haystack
  while [[ $rest == *"$needle"* ]]; do
    out+=${rest%%"$needle"*}$repl
    rest=${rest#*"$needle"}
  done
  printf '%s' "$out$rest"
}

# Escape a value for a Rust string literal: backslash first, then quotes.
rust_escape() {
  local s=$1
  s="$(replace_all "$s" "\\" "\\\\")" # \ -> \\
  s="$(replace_all "$s" "\"" "\\\"")" # " -> \"
  printf '%s' "$s"
}

# Scrub the working-tree secrets file back to the empty checked-in example
# so real Wi-Fi / Tailscale material never sits in this open-source checkout.
scrub_secrets() {
  if [ -f "$SECRETS_EXAMPLE" ]; then
    cp "$SECRETS_EXAMPLE" "$SECRETS"
    chmod 600 "$SECRETS" 2>/dev/null || true
    echo "Scrubbed secrets.rs back to the empty example (credentials live on the device only)."
  fi
}

render_secrets() { # prints a one-shot secrets.rs for this flash only
  local ssid_e password_e tskey_e networks server_host_e server_port
  ssid_e="$(rust_escape "$SSID")"
  password_e="$(rust_escape "$PASSWORD")"
  if [ -n "$SSID" ]; then
    networks="heapless::Vec::from([WifiCredentials::new(\"${ssid_e}\", \"${password_e}\")
        .expect(\"SSID or password exceeds the bounded buffers\")])"
  else
    networks="heapless::Vec::<WifiCredentials, MAX_NETWORKS>::new()"
  fi
  if [ -n "$TSKEY" ]; then
    tskey_e="$(rust_escape "$TSKEY")"
  else
    tskey_e=""
  fi
  # Split host:port from --server (defaults stay empty → config.rs localhost).
  server_host_e=""
  server_port=8000
  if [ -n "$SERVER" ] && [ "$SERVER" != "$DEFAULT_SERVER" ]; then
    if [[ "$SERVER" == *:* ]]; then
      server_host_e="$(rust_escape "${SERVER%:*}")"
      server_port="${SERVER##*:}"
    else
      server_host_e="$(rust_escape "$SERVER")"
    fi
  fi
  cat <<EOF
//! Generated by scripts/provision-device.sh for a single USB flash.
//! Scrubbed back to secrets.rs.example after a successful flash — do not commit.

use core::ffi::CStr;

use comack_core::domain::{WifiCredentials, MAX_NETWORKS};

pub fn wifi_networks() -> heapless::Vec<WifiCredentials, MAX_NETWORKS> {
    ${networks}
}

pub const TAILSCALE_AUTH_KEY: &CStr = c"${tskey_e}";

pub const SERVER_HOST: &str = "${server_host_e}";
pub const SERVER_PORT: u16 = ${server_port};

pub const DEVICE_ID: &str = "";
pub const DEVICE_TOKEN: &str = "";
EOF
}

hash_line() { # portable sha256 of a file, or of stdin when no file is given
  if command -v sha256sum > /dev/null 2>&1; then
    sha256sum "$@" | cut -d' ' -f1
  else
    shasum -a 256 "$@" | cut -d' ' -f1
  fi
}

# Hash of the collected values (everything that decides what gets baked in).
# Fields are separated by \037, which validation keeps out of every value.
values_hash() {
  printf '%s\037%s\037%s\037%s\037%s\037%s\037%s' \
    "$SSID" "$PASSWORD" "$SERVER" "$NAME" "$TSKEY" "$NO_SECRETS" "$E1002" | hash_line
}

current_secrets_hash() {
  if [ -f "$SECRETS" ]; then
    hash_line "$SECRETS"
  else
    printf ''
  fi
}

TMP_FILE=""
WROTE_SECRETS=0
cleanup() {
  if [ -n "$TMP_FILE" ]; then
    rm -f "$TMP_FILE"
  fi
  # Never leave a populated secrets.rs behind if this run wrote one
  # (Ctrl-C mid-build, failed flash, --no-flash, …).
  if [ "${WROTE_SECRETS:-0}" -eq 1 ] && [ "${NO_SECRETS:-0}" -eq 0 ]; then
    scrub_secrets
  fi
}
trap cleanup EXIT

write_secrets() { # write_secrets <content>: atomic replace (gitignored; scrubbed after flash)
  local content=$1 tmp
  tmp="$(mktemp "$ESP_DIR/src/.secrets.rs.XXXXXX")"
  TMP_FILE=$tmp
  printf '%s\n' "$content" > "$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$SECRETS"
  TMP_FILE=""
  WROTE_SECRETS=1
}

echo "=== Comack device provisioning ==="

# Resolve E1001/E1002 before hashing values or building — detection may prompt.
resolve_board

# Fill anything missing: prompts interactively, hard requirement otherwise.
if [ "$NO_SECRETS" -eq 0 ]; then
  if [ -z "$SSID" ]; then
    if [ "$INTERACTIVE" -eq 1 ]; then
      SSID="$(prompt_visible "Wi-Fi SSID" "")"
    else
      die "--ssid is required (or run interactively to be prompted)"
    fi
  fi
  if [ -z "$PASSWORD" ]; then
    if [ "$INTERACTIVE" -eq 1 ]; then
      PASSWORD="$(prompt_hidden "Wi-Fi password (input hidden)")"
    else
      die "--password is required (or run interactively to be prompted)"
    fi
  fi
fi
if [ -z "$SERVER" ]; then
  if [ "$INTERACTIVE" -eq 1 ]; then
    SERVER="$(prompt_visible "Server address (hostname or ip:port)" "$DEFAULT_SERVER")"
  else
    SERVER=$DEFAULT_SERVER
  fi
fi
if [ -z "$NAME" ]; then
  if [ "$INTERACTIVE" -eq 1 ]; then
    NAME="$(prompt_visible "Device name" "$DEFAULT_NAME")"
  else
    NAME=$DEFAULT_NAME
  fi
fi
if [ -z "$TSKEY" ] && [ "$INTERACTIVE" -eq 1 ]; then
  TSKEY="$(prompt_hidden "Tailscale auth key (empty to skip)")"
fi

if [ "$NO_SECRETS" -eq 0 ]; then
  validate_ssid "$SSID"
  validate_password "$PASSWORD"
fi
validate_server "$SERVER"
validate_name "$NAME"
validate_tskey "$TSKEY"

# Nothing to do? Check before touching any tooling so the no-op run works
# on machines without the ESP toolchain. The state records two hashes: the
# collected values, and the secrets.rs bytes of the last scripted flash —
# the latter tells a script-owned file apart from a hand edit later on.
stored_values="" stored_secrets=""
if [ -f "$STATE_FILE" ]; then
  stored_values="$(sed -n 's/^PROVISION_VALUES=//p' "$STATE_FILE")"
  stored_secrets="$(sed -n 's/^PROVISION_SECRETS=//p' "$STATE_FILE")"
fi
values_now="$(values_hash)"
secrets_now="$(current_secrets_hash)"
if [ "$FORCE" -eq 0 ] && [ "$NO_FLASH" -eq 0 ] && [ -n "$stored_values" ] \
  && [ "$stored_values" = "$values_now" ] && [ "$stored_secrets" = "$secrets_now" ]; then
  if [ "$MONITOR" -eq 1 ]; then
    echo "Nothing to flash (unchanged since the last successful flash); opening the serial monitor."
    monitor_args=(monitor)
    if [ -n "$SERIAL_PORT" ]; then
      monitor_args+=(--port "$SERIAL_PORT")
    fi
    exec espflash "${monitor_args[@]}"
  fi
  echo "Nothing to do: the device was already flashed with these values."
  echo "(Pass --force to build and flash again.)"
  exit 0
fi

# The Xtensa toolchain installed by espup is activated via ~/export-esp.sh;
# a missing file is fine (the tools may already be on PATH, e.g. in CI).
if [ -f "$HOME/export-esp.sh" ]; then
  # shellcheck source=/dev/null
  . "$HOME/export-esp.sh"
fi
missing=""
for tool in cargo ldproxy espflash; do
  if ! command -v "$tool" > /dev/null 2>&1; then
    missing="$missing $tool"
  fi
done
if [ -n "$missing" ]; then
  die "missing toolchain:$missing

Install the ESP build tools once (see firmware/README.md):
  cargo install espup ldproxy espflash
  espup install            # Xtensa Rust toolchain
  . ~/export-esp.sh        # activate it, then re-run this script"
fi

if [ "$NAME" != "$DEFAULT_NAME" ]; then
  echo "note: device name is still the config.rs constant DEVICE_NAME"
  echo "      (default $DEFAULT_NAME). Yours: $NAME — edit"
  echo "      firmware/crates/comack-core/src/config.rs or set it in the"
  echo "      on-device portal after first boot."
fi
if [ -n "$SERVER" ] && [ "$SERVER" != "$DEFAULT_SERVER" ]; then
  echo "note: server $SERVER will be baked into this flash via secrets.rs"
  echo "      (scrubbed from the tree after flash)."
fi

if [ "$NO_SECRETS" -eq 0 ]; then
  if [ ! -f "$SECRETS_EXAMPLE" ]; then
    die "template not found: $SECRETS_EXAMPLE"
  fi
  rendered="$(render_secrets)"
  if [ -f "$SECRETS" ]; then
    on_disk="$(cat "$SECRETS")"
    if [ "$on_disk" = "$rendered" ]; then
      echo "secrets.rs already matches the collected values (unchanged)"
    else
      overwrite=0
      owned=0
      disk_hash="$(hash_line "$SECRETS")"
      if [ -n "$stored_secrets" ] && [ "$disk_hash" = "$stored_secrets" ]; then
        owned=1 # the file is exactly what our last scripted flash wrote
      fi
      if [ "$owned" -eq 1 ] || [ "$FORCE_SECRETS" -eq 1 ]; then
        overwrite=1
      elif [ "$INTERACTIVE" -eq 1 ]; then
        printf '%s exists and differs from the values collected here.\n' "$SECRETS" >&2
        printf 'Overwrite it? [y/N] ' >&2
        if ! read -r answer; then
          die "no answer — pass --force-secrets to overwrite without asking"
        fi
        if [ "$answer" = "y" ] || [ "$answer" = "Y" ]; then
          overwrite=1
        fi
      fi
      if [ "$overwrite" -eq 1 ]; then
        write_secrets "$rendered"
        if [ "$owned" -eq 1 ]; then
          echo "Updated $SECRETS with the new values (replacing the previous scripted render)"
        else
          echo "Wrote $SECRETS (replaced an existing, differing file)"
        fi
      else
        die "$SECRETS exists with different contents; pass --force-secrets to overwrite it"
      fi
    fi
  else
    write_secrets "$rendered"
    echo "Wrote $SECRETS from the checked-in example"
  fi
else
  if [ ! -f "$SECRETS" ]; then
    die "--no-secrets passed but $SECRETS does not exist; create it first (see firmware/README.md) or drop --no-secrets"
  fi
  echo "Leaving secrets.rs untouched (--no-secrets)"
fi

FEATURES=""
if [ "$E1002" -eq 1 ]; then
  FEATURES="--features e1002"
fi

if [ "$NO_FLASH" -eq 1 ]; then
  echo "Building firmware (--no-flash: nothing will be flashed)…"
  # shellcheck disable=SC2086 # FEATURES is two words on purpose
  (cd "$ESP_DIR" && cargo build --release $FEATURES)
  if [ ! -f "$ELF" ]; then
    die "build finished but the image is missing: $ELF"
  fi
  echo "Build complete: $ELF"
  echo "Provisioning state not recorded (--no-flash); run again without --no-flash to flash."
  exit 0
fi

echo "Building firmware (first build downloads and compiles ESP-IDF v5.5.5)…"
# shellcheck disable=SC2086 # FEATURES is two words on purpose
(cd "$ESP_DIR" && cargo build --release $FEATURES)
if [ ! -f "$ELF" ]; then
  die "build finished but the image is missing: $ELF"
fi

flash_args=(flash --partition-table "$ESP_DIR/partitions.csv" "$ELF")
if [ -n "$SERIAL_PORT" ]; then
  flash_args+=(--port "$SERIAL_PORT")
fi
if [ "$MONITOR" -eq 1 ]; then
  flash_args+=(--monitor)
fi
echo "Flashing over USB${SERIAL_PORT:+ on $SERIAL_PORT}…"
  if ! espflash "${flash_args[@]}"; then
  die "espflash failed — check the USB cable and serial permissions (dialout group); see firmware/README.md"
fi

# Credentials were only needed to bake the image; the flash carries them
# now. Scrub here (and clear the flag so EXIT does not print twice).
if [ "$NO_SECRETS" -eq 0 ]; then
  scrub_secrets
  WROTE_SECRETS=0
fi

{
  printf 'PROVISION_VALUES=%s\n' "$values_now"
  # Hash of the scrubbed (empty) file — next run must re-render to flash again.
  printf 'PROVISION_SECRETS=%s\n' "$(current_secrets_hash)"
} > "$STATE_FILE"

# Remember MAC→board so the next flash can skip the slow detect path.
remember_args=(--remember "$( [ "$E1002" -eq 1 ] && echo e1002 || echo e1001 )")
if [ -n "$SERIAL_PORT" ]; then
  remember_args+=(--serial-port "$SERIAL_PORT")
fi
"$DETECT_SCRIPT" "${remember_args[@]}" >/dev/null || warn "could not cache board identity for next flash"

echo ""
echo "Provisioned. The device will boot with the compiled-in settings."
echo "Next steps:"
if [ -n "$SERIAL_PORT" ]; then
  echo "  - watch the serial log: espflash monitor --port $SERIAL_PORT"
else
  echo "  - watch the serial log: espflash monitor"
fi
echo "  - later Wi-Fi changes need no USB: hold all 3 buttons 10 s to reopen"
echo "    the on-device portal (see firmware/README.md)"
