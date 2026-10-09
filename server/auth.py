"""
Optional bearer-token auth for the browser-facing surfaces (WEB_TOKEN).

The split, kept stable for the hardware and the browser:
- Always open (device-critical): the audio stream (/ws/audio), every comic
  read the device or a browser <img> tag makes — current-comic status/image/bin
  and per-issue png/bin/originals — the device's press reports
  (/api/device/buttons), the HTML shells / and /reader, and
  /static/**. The e-paper appliance must keep working with WEB_TOKEN set and
  no header capability: the firmware streams to /ws/audio and polls the
  current-comic endpoints with nothing but the path. And a browser cannot
  attach Authorization headers to top-level navigation, so a gated shell
  would 401 the page itself and the in-page token challenge could never
  boot — the shells are the same static code /static/ already serves
  openly, while every /api and /ws request behind them stays token-gated.
- Token required when WEB_TOKEN is set: everything else — all /api routes and
  the /ws/events feed. Browsers send "Authorization: Bearer <token>" via
  fetch(); the event feed WebSocket, which cannot set headers, appends
  ?token= to the URL. A tokenless page load renders the shell, gets 401s
  from the API, and prompts for the token before anything sensitive loads.

With WEB_TOKEN unset (the default) everything is open: the appliance assumes a
single-user network such as a tailnet. The comparison is constant-time and the
classification is pure so tests cover the exact paths, not a live server.
"""
import hmac
import re

# Paths that stay tokenless so the hardware and <img> tags keep working.
DEVICE_PATH_PATTERNS = (
    re.compile(r"^/api/comics/current(\.png|\.bin)?$"),
    re.compile(r"^/api/comics/\d+/png$"),
    re.compile(r"^/api/comics/\d+/bin$"),
    re.compile(r"^/api/comics/\d+/(color|panel\d+)\.png$"),
    re.compile(r"^/api/device/buttons$"),
)
# Code-only surfaces behind the token-gated APIs, plus the device's audio stream.
OPEN_PREFIXES = ("/static/", "/ws/audio")
# The HTML shells: same code as /static/, loadable without headers so the
# in-page token challenge can run.
OPEN_EXACT = frozenset({"/", "/reader"})

BEARER_PREFIX = "Bearer "


def is_open_path(path: str) -> bool:
    """True for paths that never require a token (HTML shells, device + static)."""
    if path in OPEN_EXACT or path.startswith(OPEN_PREFIXES):
        return True
    return any(pattern.match(path) for pattern in DEVICE_PATH_PATTERNS)


def token_matches(provided: str, expected: str) -> bool:
    """Constant-time comparison of a presented token against WEB_TOKEN."""
    return bool(expected) and hmac.compare_digest(provided.encode(), expected.encode())


def authorized(path: str, authorization_header: str | None, query_token: str | None) -> bool:
    """Auth decision for an HTTP request. Auth disabled (empty WEB_TOKEN) or an
    open path -> allow; otherwise require a Bearer header. The query-token form
    is accepted only for the event-feed WebSocket path, so tokens never land in
    access logs for ordinary requests."""
    from server.config import settings  # read per call: tests monkeypatch the live singleton

    if not settings.WEB_TOKEN or is_open_path(path):
        return True
    header = authorization_header or ""
    if header.startswith(BEARER_PREFIX) and token_matches(header[len(BEARER_PREFIX):], settings.WEB_TOKEN):
        return True
    if path == "/ws/events" and query_token:
        return token_matches(query_token, settings.WEB_TOKEN)
    return False
