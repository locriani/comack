"""
Connected-device registry behind GET /api/devices.

Answers "is my device actually connected?" from the server side: every live
WebSocket connection (the device's /ws/audio stream, dashboard /ws/events
subscribers) is tracked from accept() to close with the peer IP and the last
time it delivered traffic. Entries are keyed by socket identity — two browser
tabs are two rows, and the device's audio stream is the row that matters for
reachability (the tokenless firmware never touches /ws/events when WEB_TOKEN
is set).

Nothing here interprets traffic or reads headers beyond the optional ?name=
query parameter: with no client-supplied name the connection is labeled by its
role, because the firmware cannot identify itself yet (no header capability —
server/auth.py). Cleanup is event-driven on disconnect, the same lifecycle the
event_subscribers set has always used; a connection that dies without a close
frame is reaped when its socket eventually errors.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List

# Bound on the client-supplied display name, so one connection cannot pad a
# /api/devices response (single-user appliance, but the bound is free).
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


class DeviceRegistry:
    """Live WebSocket connections, keyed by socket identity.

    Times are epoch seconds internally and rendered to ISO 8601 UTC only in
    snapshot(), so tests can drive the clock instead of parsing dates.
    """

    def __init__(self) -> None:
        self._connections: Dict[Any, Dict[str, Any]] = {}

    def connect(self, ws: Any, name: str, ip: str, now: float) -> None:
        """Record a freshly accepted connection; connected_since and last_seen start together."""
        self._connections[ws] = {
            "name": name,
            "ip": ip,
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
                "connected_since": iso_utc(connection["connected_since"]),
                "last_seen": iso_utc(connection["last_seen"]),
            }
            for _, connection in by_connected_since
        ]


device_registry = DeviceRegistry()
