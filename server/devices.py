"""
Device identity (the `devices` registry) plus live-connection visibility.

Identity (spec D1): the `devices` table is the sole identity record — a
server-issued id, a user-visible name, a hashed per-device token, and
last-seen. resolve_device() gives every /ws/audio connection exactly three
outcomes: a valid ?device_id=&device_token= pair binds the stream to that
device; absent identity binds the auto-created 'default' device (today's
tokenless firmware keeps working unchanged); identity presented but wrong
closes the connection with 4401 — never a silent fallback, so a mistyped
token cannot quietly route one room's audio into another room's story.
Tokens are shown once at creation, stored as SHA-256, and compared
constant-time — the same discipline as WEB_TOKEN (server/auth.py), one step
stricter: hashed at rest, because unlike WEB_TOKEN these sit in the database.

Live visibility (GET /api/devices): every connection is tracked from accept()
to close with the peer IP and the last time it delivered traffic, now
attributed to its resolved device. Entries are keyed by socket identity — two
browser tabs are two rows, and the device's audio stream is the row that
matters for reachability (the tokenless firmware never touches /ws/events when
WEB_TOKEN is set). Cleanup is event-driven on disconnect, the same lifecycle
the event_subscribers set has always used; a connection that dies without a
close frame is reaped when its socket eventually errors.
"""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from server.storage import DEFAULT_DEVICE_ID, db

# Bound on user-visible device names, so one device cannot pad a /api/devices
# response (single-user appliance, but the bound is free).
MAX_NAME_LENGTH = 64

# Role labels for connections that do not send ?name=.
AUDIO_STREAMER = "audio streamer"
EVENT_SUBSCRIBER = "event subscriber"


def iso_utc(ts: float) -> str:
    """Epoch seconds -> ISO 8601 UTC with a Z suffix (2026-10-09T18:22:04Z)."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def display_name(raw: str | None, fallback: str) -> str:
    """The client-supplied ?name= value, trimmed and bounded, or the role label."""
    if raw:
        cleaned = raw.strip()[:MAX_NAME_LENGTH]
        if cleaned:
            return cleaned
    return fallback


# --- Identity: the devices registry (spec D1) ---

def generate_device_token() -> str:
    """A fresh per-device credential: 256 bits of URL-safe randomness."""
    return secrets.token_urlsafe(32)


def hash_device_token(token: str) -> str:
    """SHA-256 hex digest. The token is high-entropy random (not a password
    anyone could guess), so a plain digest at rest is the right cost — no KDF,
    no salt surface, nothing to get wrong; compare_digest does the rest."""
    return hashlib.sha256(token.encode()).hexdigest()


def slugify_device_id(name: str) -> str:
    """A name-derived id candidate: lowercase, [a-z0-9-] only, room for a -N suffix."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48]


def unique_device_id(name: str) -> str:
    """Server-issued device id: derived from the name, deduped against the registry."""
    base = slugify_device_id(name) or "device"  # all-unicode names still get an id
    candidate, n = base, 2
    while db.get_device(candidate) is not None:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def public_device(row: Dict[str, Any]) -> Dict[str, Any]:
    """A device row for API responses: never the token hash; ISO times like the live rows."""
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": iso_utc(row["created_at"]),
        "last_seen_at": iso_utc(row["last_seen_at"]) if row["last_seen_at"] is not None else None,
    }


def resolve_device(device_id: Optional[str], device_token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Which device does this connection belong to? Three outcomes, no exceptions:

    - valid pair -> that device;
    - no identity (neither param present) -> the auto 'default' device, so
      today's tokenless firmware and every existing deployment keep working;
    - identity presented but wrong (unknown id, bad or missing token) -> None:
      the caller closes 4401. Never a silent fallback to default — a mistyped
      token must not quietly route one room's audio into another room's story.
    """
    if not device_id and not device_token:
        return db.get_device(DEFAULT_DEVICE_ID)
    if device_id and device_token and db.check_device_token_hash(device_id, hash_device_token(device_token)):
        return db.get_device(device_id)
    return None


class DeviceRegistry:
    """Live WebSocket connections, keyed by socket identity.

    Times are epoch seconds internally and rendered to ISO 8601 UTC only in
    snapshot(), so tests can drive the clock instead of parsing dates.
    """

    def __init__(self) -> None:
        self._connections: Dict[Any, Dict[str, Any]] = {}

    def connect(self, ws: Any, name: str, ip: str, now: float, device_id: Optional[str] = None) -> None:
        """Record a freshly accepted connection; connected_since and last_seen start together.
        device_id is the resolved registry id, or None for connections that present no
        device identity (dashboard event subscribers)."""
        self._connections[ws] = {
            "name": name,
            "ip": ip,
            "device_id": device_id,
            "connected_since": now,
            "last_seen": now,
        }

    def touch(self, ws: Any, now: float) -> None:
        """Note traffic from a connection (audio chunks, event-feed pings)."""
        connection = self._connections.get(ws)
        if connection is not None:
            connection["last_seen"] = now

    def disconnect(self, ws: Any) -> None:
        """Drop a connection — safe to call more than once (finally-block cleanup)."""
        self._connections.pop(ws, None)

    def snapshot(self) -> List[Dict[str, str]]:
        """Currently-connected devices, oldest connection first, ISO 8601 UTC times."""
        by_connected_since = sorted(self._connections.items(), key=lambda item: item[1]["connected_since"])
        return [
            {
                "name": connection["name"],
                "ip": connection["ip"],
                "device_id": connection["device_id"],
                "connected_since": iso_utc(connection["connected_since"]),
                "last_seen": iso_utc(connection["last_seen"]),
            }
            for _, connection in by_connected_since
        ]


device_registry = DeviceRegistry()
