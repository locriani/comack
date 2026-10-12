#!/usr/bin/env bash
# Detect whether the USB-attached reTerminal is an E1001 or E1002.
#
# USB VID/PID and the ESP32-S3 are identical between the two; the panel is
# the only difference. Detection therefore uses, in order:
#   1. a MAC→board cache written after a successful flash
#   2. the COMACK_BOARD= marker in a short flash scan / boot log
#   3. (legacy) the old "COMACK: reTerminal E100x" boot line
#
# Prints `e1001` or `e1002` on stdout. Diagnostics go to stderr.
# Exit 0 on success, 1 when the board cannot be determined.
set -euo pipefail

# espflash is usually installed here; don't require the caller to have set PATH.
export PATH="${HOME:+$HOME/.cargo/bin:}$PATH"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_FILE="${COMACK_BOARD_CACHE:-$REPO_ROOT/firmware/esp/.board-by-mac}"
FACTORY_OFFSET=65536   # 0x10000 — matches partitions.csv
CHUNK_SIZE=65536       # 64 KiB; larger reads often corrupt on this CH340
MAX_CHUNKS=24          # ~1.5 MiB of the 3 MiB factory app
SERIAL_PORT=""
WRITE_CACHE=""
MAC_OVERRIDE=""
SELF_TEST=0

usage() {
  cat <<'EOF'
Detect the attached reTerminal board model (e1001 or e1002).

Usage: scripts/detect-reterminal-board.sh [options]

  --serial-port DEV   serial device (default: espflash auto-detect)
  --cache-file PATH   MAC→board cache (default: firmware/esp/.board-by-mac)
  --remember BOARD    write MAC→BOARD into the cache and exit (e1001|e1002)
  --mac MAC           use this MAC with --remember (skip board-info)
  --self-test         run parser/cache checks (no hardware) and exit
  --help              show this help

Stdout is exactly one token: e1001 or e1002. Everything else is on stderr.
EOF
}

die() { echo "error: $*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --serial-port) [ "$#" -ge 2 ] || die "--serial-port needs a value"; SERIAL_PORT=$2; shift 2 ;;
    --cache-file)  [ "$#" -ge 2 ] || die "--cache-file needs a value"; CACHE_FILE=$2; shift 2 ;;
    --remember)    [ "$#" -ge 2 ] || die "--remember needs e1001 or e1002"; WRITE_CACHE=$2; shift 2 ;;
    --mac)         [ "$#" -ge 2 ] || die "--mac needs a value"; MAC_OVERRIDE=$2; shift 2 ;;
    --self-test)   SELF_TEST=1; shift ;;
    --help|-h)     usage; exit 0 ;;
    *)             die "unknown argument: $1 (see --help)" ;;
  esac
done

# Extract a board id from free-form text (flash dump, boot log, …).
# Prefers the stable COMACK_BOARD= marker; falls back to the legacy boot line.
parse_board_marker() {
  local text=$1
  if [[ $text =~ COMACK_BOARD=(e100[12]) ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return 0
  fi
  # Legacy firmware always logged "E1001" even for e1002 builds — only trust
  # an explicit E1002 hit from that era; E1001 is ambiguous.
  if [[ $text =~ reTerminal[[:space:]]+E1002 ]]; then
    printf 'e1002'
    return 0
  fi
  return 1
}

normalize_mac() {
  # Lowercase, colon-separated; accept aa:bb:… or aabb…
  local raw=$1
  raw=$(printf '%s' "$raw" | tr 'A-F' 'a-f' | tr -d '[:space:]')
  if [[ $raw =~ ^([0-9a-f]{2}:){5}[0-9a-f]{2}$ ]]; then
    printf '%s' "$raw"
    return 0
  fi
  if [[ $raw =~ ^[0-9a-f]{12}$ ]]; then
    printf '%s' "$raw" | sed 's/../&:/g; s/:$//'
    return 0
  fi
  return 1
}

cache_get() {
  local mac=$1
  [ -f "$CACHE_FILE" ] || return 1
  local board
  board=$(sed -n "s/^${mac}=//p" "$CACHE_FILE" | head -1)
  case "$board" in
    e1001|e1002) printf '%s' "$board"; return 0 ;;
  esac
  return 1
}

cache_put() {
  local mac=$1 board=$2
  mkdir -p "$(dirname "$CACHE_FILE")"
  touch "$CACHE_FILE"
  if grep -q "^${mac}=" "$CACHE_FILE" 2>/dev/null; then
    local tmp
    tmp=$(mktemp)
    sed "s/^${mac}=.*/${mac}=${board}/" "$CACHE_FILE" > "$tmp"
    mv -f "$tmp" "$CACHE_FILE"
  else
    printf '%s=%s\n' "$mac" "$board" >> "$CACHE_FILE"
  fi
}

espflash_args=()
if [ -n "$SERIAL_PORT" ]; then
  espflash_args+=(--port "$SERIAL_PORT")
fi

require_espflash() {
  command -v espflash >/dev/null 2>&1 || die "espflash not on PATH (cargo install espflash)"
}

read_mac() {
  if [ -n "$MAC_OVERRIDE" ]; then
    normalize_mac "$MAC_OVERRIDE" || die "invalid --mac value: $MAC_OVERRIDE"
    return 0
  fi
  require_espflash
  local info mac
  info=$(espflash board-info "${espflash_args[@]}" 2>&1) || die "espflash board-info failed — is the device plugged in?"
  mac=$(printf '%s\n' "$info" | sed -n 's/^MAC address:[[:space:]]*//p' | head -1)
  [ -n "$mac" ] || die "could not parse MAC from espflash board-info"
  normalize_mac "$mac" || die "unrecognised MAC from board-info: $mac"
}

# Sniff a few seconds of boot log after reset (no Python dependency).
sniff_boot_log() {
  require_espflash
  local out pid
  out=$(mktemp)
  # monitor resets the chip on connect and streams the UART.
  espflash monitor "${espflash_args[@]}" >"$out" 2>&1 &
  pid=$!
  sleep 5
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  cat "$out"
  rm -f "$out"
}

# Scan the factory app partition for the contiguous COMACK_BOARD= marker.
scan_flash_marker() {
  require_espflash
  local i offset tmp hit
  tmp=$(mktemp)
  for i in $(seq 0 $((MAX_CHUNKS - 1))); do
    offset=$((FACTORY_OFFSET + i * CHUNK_SIZE))
    if ! espflash read-flash "${espflash_args[@]}" --baud 115200 \
        "$offset" "$CHUNK_SIZE" "$tmp" >/dev/null 2>&1; then
      continue
    fi
    hit=$(strings "$tmp" 2>/dev/null | tr '\n' ' ')
    if board=$(parse_board_marker "$hit"); then
      rm -f "$tmp"
      printf '%s' "$board"
      return 0
    fi
  done
  rm -f "$tmp"
  return 1
}

# --- --self-test (no hardware) ---------------------------------------------
if [ "$SELF_TEST" -eq 1 ]; then
  fail=0
  expect() {
    local want=$1 got
    shift
    got=$("$@") || got=""
    if [ "$got" != "$want" ]; then
      echo "FAIL: $* → '$got' (want '$want')" >&2
      fail=1
    fi
  }
  expect e1002 parse_board_marker "I (123) COMACK_BOARD=e1002; reTerminal display"
  expect e1001 parse_board_marker "noise COMACK_BOARD=e1001 trailing"
  expect e1002 parse_board_marker "COMACK: reTerminal E1002 display, native"
  # Legacy E1001 boot line is ambiguous (old e1002 builds lied) — must not match.
  if parse_board_marker "COMACK: reTerminal E1001 display, native" >/dev/null; then
    echo "FAIL: legacy E1001 line should be ignored" >&2
    fail=1
  fi
  expect "ac:27:6e:a6:a2:40" normalize_mac "AC:27:6E:A6:A2:40"
  expect "ac:27:6e:a6:a2:40" normalize_mac "ac276ea6a240"
  CACHE_FILE=$(mktemp)
  cache_put "ac:27:6e:a6:a2:40" "e1002"
  expect e1002 cache_get "ac:27:6e:a6:a2:40"
  cache_put "ac:27:6e:a6:a2:40" "e1001"
  expect e1001 cache_get "ac:27:6e:a6:a2:40"
  rm -f "$CACHE_FILE"
  [ "$fail" -eq 0 ] || die "self-test failed"
  echo "self-test ok" >&2
  exit 0
fi

# --- --remember path --------------------------------------------------------
if [ -n "$WRITE_CACHE" ]; then
  case "$WRITE_CACHE" in
    e1001|e1002) ;;
    *) die "--remember needs e1001 or e1002" ;;
  esac
  MAC=$(read_mac)
  cache_put "$MAC" "$WRITE_CACHE"
  echo "remembered $MAC → $WRITE_CACHE (cache: $CACHE_FILE)" >&2
  printf '%s\n' "$WRITE_CACHE"
  exit 0
fi

# --- detect path ------------------------------------------------------------
MAC=$(read_mac)
echo "device MAC: $MAC" >&2

if board=$(cache_get "$MAC"); then
  echo "board from cache: $board" >&2
  printf '%s\n' "$board"
  exit 0
fi

echo "no cache entry; sniffing boot log…" >&2
if boot=$(sniff_boot_log) && board=$(parse_board_marker "$boot"); then
  echo "board from boot log: $board" >&2
  cache_put "$MAC" "$board"
  printf '%s\n' "$board"
  exit 0
fi

echo "boot log inconclusive; scanning flash for COMACK_BOARD=…" >&2
if board=$(scan_flash_marker); then
  echo "board from flash: $board" >&2
  cache_put "$MAC" "$board"
  printf '%s\n' "$board"
  exit 0
fi

die "could not detect board (E1001 vs E1002). Pass --e1001 or --e1002 to provision-device.sh, or flash once with an explicit board so the COMACK_BOARD= marker is present."
