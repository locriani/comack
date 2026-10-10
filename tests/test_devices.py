"""GET/POST/PATCH /api/devices: the device registry plus live-connection visibility.

Identity (spec D1): registered devices — server-issued id, name, hashed token,
last-seen — with the token shown exactly once at creation. Live visibility
(connected-device visibility, carried forward): every connection is tracked
with its peer IP and last_seen, now attributed to its resolved device —
tokenless /ws/audio streams bind the auto 'default' device, /ws/events
subscribers (which present no device identity) are unregistered_connections.

The endpoint is part of the token-gated browser API (server/auth.py is
default-deny for unlisted /api paths), so with WEB_TOKEN set a tokenless
request is a 401 — device data must not leak on an open network.

The registry itself is pure (server/devices.py) and covered without a server;
the route tests use TestClient without the lifespan (no models are loaded —
the fake device only connects, it does not stream).
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import server.storage as st
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server import main as main_module
from server.config import settings
from server.devices import (
    AUDIO_STREAMER,
    DeviceRegistry,
    display_name,
    hash_device_token,
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


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """The /ws/audio handler and the registry endpoints read the devices
    table; isolate per test like test_storage.py (the db singleton reads
    storage.DB_PATH at call time)."""
    monkeypatch.setattr(st, "DB_PATH", Path(tempfile.mkdtemp()) / "devices.db")
    st.init_db()
    yield


def register_device(name="Living Room"):
    """A known device for gate tests; returns (device_id, token) — the id
    derived from the name exactly as POST /api/devices derives it."""
    from server.devices import generate_device_token, unique_device_id

    token = generate_device_token()
    device_id = unique_device_id(name)
    st.db.create_device(device_id, name, hash_device_token(token))
    return device_id, token


@pytest.fixture
def client():
    return TestClient(app)


class TestDeviceRegistry:
    def test_connect_touch_disconnect_round_trip(self):
        registry = DeviceRegistry()
        registry.connect("ws", name="audio streamer", ip="100.101.102.103", now=1000.0, device_id="default")
        registry.touch("ws", now=1250.0)
        assert registry.snapshot() == [
            {
                "name": "audio streamer",
                "ip": "100.101.102.103",
                "device_id": "default",
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
    def test_tokenless_audio_binds_the_default_device(self, client):
        with client.websocket_connect("/ws/audio"):  # fake device: connect and hold
            body = client.get("/api/devices").json()
        assert [d["id"] for d in body["devices"]] == ["default"]
        device = body["devices"][0]
        assert len(device["connections"]) == 1
        connection = device["connections"][0]
        assert connection["name"] == AUDIO_STREAMER
        assert connection["ip"]  # the peer the server saw
        assert connection["connected_since"] == connection["last_seen"]  # no traffic yet
        assert connection["connected_since"].endswith("Z")
        assert body["unregistered_connections"] == []

    def test_named_tokenless_device_keeps_its_label(self, client):
        with client.websocket_connect("/ws/audio?name=comack-display"):
            body = client.get("/api/devices").json()
        default = next(d for d in body["devices"] if d["id"] == "default")
        assert [c["name"] for c in default["connections"]] == ["comack-display"]

    def test_device_disappears_after_disconnect(self, client):
        with client.websocket_connect("/ws/audio"):
            pass
        body = client.get("/api/devices").json()
        assert all(d["connections"] == [] for d in body["devices"])
        assert body["unregistered_connections"] == []

    def test_event_subscribers_are_unregistered_connections(self, client):
        class FakeDispatcher:
            def get_status(self):
                return {"is_speaking": False}

        class FakePipeline:
            dispatcher = FakeDispatcher()

        class FakePipelines:
            def for_device(self, device_id):
                return FakePipeline()

        with patch.object(main_module, "pipelines", FakePipelines()):
            with client.websocket_connect("/ws/events?name=dashboard") as ws:
                ws.receive_json()  # INIT_SNAPSHOT
                body = client.get("/api/devices").json()
        assert [c["name"] for c in body["unregistered_connections"]] == ["dashboard"]
        assert all(d["connections"] == [] for d in body["devices"])

    def test_identified_stream_is_attributed_to_its_device(self, client):
        device_id, token = register_device("Den")
        with client.websocket_connect(f"/ws/audio?device_id={device_id}&device_token={token}"):
            body = client.get("/api/devices").json()
        den = next(d for d in body["devices"] if d["id"] == "den")
        assert [c["name"] for c in den["connections"]] == ["Den"]
        assert den["last_seen_at"] is not None  # touched on connect


class TestAudioIdentityGate:
    """Identity presented but wrong closes 4401 — never a silent fallback to default."""

    def test_wrong_token_is_4401(self, client):
        register_device()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/audio?device_id=living-room&device_token=wrong"):
                pass

    def test_unknown_device_id_is_4401(self, client):
        register_device()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/audio?device_id=ghost-room&device_token=x"):
                pass

    def test_id_without_token_is_4401(self, client):
        register_device()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/audio?device_id=living-room"):
                pass

    def test_a_rejected_connection_is_never_bound_to_default(self, client):
        register_device()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/audio?device_id=living-room&device_token=wrong"):
                pass
        body = client.get("/api/devices").json()  # the mistyped token must not
        assert all(  # have routed the stream into the default device's ear
            c["name"] != AUDIO_STREAMER
            for c in body["devices"][0]["connections"] + body["unregistered_connections"]
        )


class TestRegistryEndpoints:
    def test_create_returns_the_token_exactly_once(self, client):
        resp = client.post("/api/devices", json={"name": "Living Room"})
        assert resp.status_code == 200
        created = resp.json()
        assert created["id"] == "living-room"
        assert created["name"] == "Living Room"
        assert len(created["token"]) >= 32

        body = client.get("/api/devices").json()
        assert [d["id"] for d in body["devices"]] == ["default", "living-room"]
        listed = body["devices"][1]
        assert listed["created_at"].endswith("Z")
        assert listed["last_seen_at"] is None  # never connected yet
        assert "token" not in json.dumps(body)  # shown once: never listed again, hash included

    def test_duplicate_names_get_distinct_ids(self, client):
        first = client.post("/api/devices", json={"name": "Living Room"}).json()
        second = client.post("/api/devices", json={"name": "Living Room"}).json()
        assert (first["id"], second["id"]) == ("living-room", "living-room-2")

    def test_create_rejects_blank_name(self, client):
        assert client.post("/api/devices", json={"name": "   "}).status_code == 400

    def test_create_rejects_overlong_name(self, client):
        assert client.post("/api/devices", json={"name": "x" * 65}).status_code == 400

    def test_rename_updates_the_label(self, client):
        client.post("/api/devices", json={"name": "Living Room"})
        resp = client.patch("/api/devices/living-room", json={"name": "Den"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Den"
        assert "token" not in json.dumps(resp.json())
        body = client.get("/api/devices").json()
        assert [d["name"] for d in body["devices"]] == ["Default", "Den"]

    def test_rename_unknown_device_is_404(self, client):
        assert client.patch("/api/devices/ghost", json={"name": "X"}).status_code == 404

    def test_rename_rejects_blank_name(self, client):
        client.post("/api/devices", json={"name": "Living Room"})
        assert client.patch("/api/devices/living-room", json={"name": " "}).status_code == 400

    def test_created_device_connects_with_its_one_shown_token(self, client):
        created = client.post("/api/devices", json={"name": "Living Room"}).json()
        with client.websocket_connect(
            f"/ws/audio?device_id={created['id']}&device_token={created['token']}"
        ):
            body = client.get("/api/devices").json()
        living = next(d for d in body["devices"] if d["id"] == "living-room")
        assert len(living["connections"]) == 1  # the one-shown token is the real credential


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

    def test_create_and_rename_require_the_token_too(self, client):
        settings.WEB_TOKEN = TOKEN
        headers = {"Authorization": f"Bearer {TOKEN}"}
        assert client.post("/api/devices", json={"name": "X"}).status_code == 401
        assert client.patch("/api/devices/default", json={"name": "X"}).status_code == 401
        assert client.post("/api/devices", json={"name": "X"}, headers=headers).status_code == 200
        assert client.patch("/api/devices/default", json={"name": "Home"}, headers=headers).status_code == 200

    def test_open_when_web_token_unset(self, client):
        settings.WEB_TOKEN = ""
        assert client.get("/api/devices").status_code == 200
