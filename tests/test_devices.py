"""GET /api/devices: connected-device visibility.

A fake device that opens /ws/audio must show up with its peer IP and
last_seen; a closed socket must disappear. The endpoint is part of the
token-gated browser API (server/auth.py is default-deny for unlisted /api
paths), so with WEB_TOKEN set a tokenless request is a 401 — device data must
not leak on an open network.

The registry itself is pure (server/devices.py) and covered without a server;
the route tests use TestClient without the lifespan (no models are loaded —
the fake device only connects, it does not stream).
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from server import main as main_module
from server.config import settings
from server.devices import (
    AUDIO_STREAMER,
    DeviceRegistry,
    display_name,
    iso_utc,
)
from server.main import app

TOKEN = "s3cret-token"


@pytest.fixture(autouse=True)
def fresh_registry():
    """Handlers read main.device_registry by module-global name; swap in a
    clean one per test so connections never leak between tests."""
    with patch.object(main_module, "device_registry", DeviceRegistry()):
        yield


@pytest.fixture
def client():
    return TestClient(app)


class TestDeviceRegistry:
    def test_connect_touch_disconnect_round_trip(self):
        registry = DeviceRegistry()
        registry.connect("ws", name="audio streamer", ip="100.101.102.103", now=1000.0)
        registry.touch("ws", now=1250.0)
        assert registry.snapshot() == [
            {
                "name": "audio streamer",
                "ip": "100.101.102.103",
                "connected_since": iso_utc(1000.0),
                "last_seen": iso_utc(1250.0),
            }
        ]
        registry.disconnect("ws")
        assert registry.snapshot() == []

    def test_touch_is_ignored_for_unknown_sockets(self):
        registry = DeviceRegistry()
        registry.touch("ghost", now=5.0)  # must not raise
        assert registry.snapshot() == []

    def test_disconnect_is_idempotent(self):
        registry = DeviceRegistry()
        registry.connect("ws", name="n", ip="1.2.3.4", now=0.0)
        registry.disconnect("ws")
        registry.disconnect("ws")
        assert registry.snapshot() == []

    def test_snapshot_is_oldest_connection_first(self):
        registry = DeviceRegistry()
        registry.connect("second", name="b", ip="2.2.2.2", now=2000.0)
        registry.connect("first", name="a", ip="1.1.1.1", now=1000.0)
        assert [device["name"] for device in registry.snapshot()] == ["a", "b"]

    def test_iso_utc_uses_z_suffix(self):
        assert iso_utc(0.0) == "1970-01-01T00:00:00Z"


class TestDisplayName:
    def test_client_supplied_name_wins(self):
        assert display_name("comack-display", "fallback") == "comack-display"

    def test_name_is_trimmed_and_bounded(self):
        assert display_name("  spaced  ", "f") == "spaced"
        assert display_name("x" * 100, "f") == "x" * 64

    def test_blank_or_missing_name_falls_back_to_role(self):
        assert display_name(None, "fallback") == "fallback"
        assert display_name("   ", "fallback") == "fallback"


class TestDevicesEndpoint:
    def test_connected_device_appears_with_ip_and_last_seen(self, client):
        with client.websocket_connect("/ws/audio"):  # fake device: connect and hold
            devices = client.get("/api/devices").json()["devices"]
        assert len(devices) == 1
        device = devices[0]
        assert device["name"] == AUDIO_STREAMER
        assert device["ip"]  # the peer the server saw
        assert device["connected_since"] == device["last_seen"]  # no traffic yet
        assert device["connected_since"].endswith("Z")

    def test_named_device_reports_its_name(self, client):
        with client.websocket_connect("/ws/audio?name=comack-display"):
            devices = client.get("/api/devices").json()["devices"]
        assert [device["name"] for device in devices] == ["comack-display"]

    def test_device_disappears_after_disconnect(self, client):
        with client.websocket_connect("/ws/audio"):
            pass
        assert client.get("/api/devices").json()["devices"] == []

    def test_event_subscribers_are_listed_too(self, client):
        class FakeDispatcher:
            def get_status(self):
                return {"is_speaking": False}

        with patch.object(main_module, "sneaky_dispatcher", FakeDispatcher()):
            with client.websocket_connect("/ws/events?name=dashboard") as ws:
                ws.receive_json()  # INIT_SNAPSHOT
                devices = client.get("/api/devices").json()["devices"]
        assert [device["name"] for device in devices] == ["dashboard"]


class TestDevicesEndpointAuth:
    @pytest.fixture(autouse=True)
    def restore_token(self):
        yield
        settings.WEB_TOKEN = ""

    def test_tokenless_request_is_401_with_web_token_set(self, client):
        settings.WEB_TOKEN = TOKEN
        assert client.get("/api/devices").status_code == 401

    def test_bearer_token_grants_access(self, client):
        settings.WEB_TOKEN = TOKEN
        resp = client.get("/api/devices", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 200

    def test_open_when_web_token_unset(self, client):
        settings.WEB_TOKEN = ""
        assert client.get("/api/devices").status_code == 200
