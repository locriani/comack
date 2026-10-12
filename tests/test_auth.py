"""Optional WEB_TOKEN auth: the device/browser path split and the middleware.

The split contract (server/auth.py):
- open without a token: the HTML shells / and /reader (a browser cannot attach
  Authorization headers to top-level navigation — the shells load so the
  in-page token challenge can run), /ws/audio, current-comic and per-issue
  png/bin/original reads, the device press reports (/api/device/buttons),
  /static/**
- everything else requires "Authorization: Bearer <token>" once WEB_TOKEN is set
  (/ws/events accepts ?token= because browsers cannot set WebSocket headers)
"""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server import main as main_module
from server.auth import authorized, is_open_path
from server.config import settings
from server.main import app

TOKEN = "s3cret-token"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def restore_token():
    yield
    settings.WEB_TOKEN = ""


class TestPathClassification:
    def test_device_critical_paths_are_open(self):
        for path in (
            "/ws/audio",
            "/api/comics/current",
            "/api/comics/current.png",
            "/api/comics/current.bin",
            "/api/comics/12/png",
            "/api/comics/12/bin",
            "/api/comics/12/color.bin",
            "/api/comics/12/color.bin/0",
            "/api/comics/12/color.bin/3",
            "/api/comics/12/color.png",
            "/api/comics/12/panel3.png",
        ):
            assert is_open_path(path), path

    def test_browser_shells_load_without_headers(self):
        # Top-level navigation cannot carry Authorization headers; the shells
        # are code (like /static/**) and stay open so the in-page token
        # challenge can boot.
        for path in ("/", "/reader"):
            assert is_open_path(path), path

    def test_browser_and_management_paths_are_protected(self):
        for path in (
            "/api/status", "/api/config", "/api/config/test/llm",
            "/api/comics/history", "/api/comics/generate", "/api/comics/12/regenerate",
            "/api/transcripts/recent", "/api/prompts", "/api/devices", "/ws/events",
        ):
            assert not is_open_path(path), path

    def test_similar_but_unlisted_paths_are_not_open(self):
        # default-deny: a new sibling route is protected unless listed
        assert not is_open_path("/api/comics/12/script")
        assert not is_open_path("/api/device/buttons/extra")
        assert not is_open_path("/staticfoo")


class TestAuthorizedDecision:
    def test_open_access_when_web_token_unset(self):
        settings.WEB_TOKEN = ""
        assert authorized("/api/config", None, None) is True

    def test_bearer_header_grants(self):
        settings.WEB_TOKEN = TOKEN
        assert authorized("/api/config", f"Bearer {TOKEN}", None) is True
        assert authorized("/api/config", "Bearer wrong", None) is False
        assert authorized("/api/config", None, None) is False

    def test_query_token_only_for_event_feed(self):
        settings.WEB_TOKEN = TOKEN
        assert authorized("/ws/events", None, TOKEN) is True
        assert authorized("/api/config", None, TOKEN) is False


class TestHttpMiddleware:
    def test_everything_open_without_web_token(self, client):
        settings.WEB_TOKEN = ""
        assert client.get("/api/config").status_code == 200
        assert client.get("/").status_code == 200

    def test_protected_endpoints_reject_anonymous(self, client):
        settings.WEB_TOKEN = TOKEN
        for path in ("/api/config", "/api/status", "/api/comics/history"):
            resp = client.get(path)
            assert resp.status_code == 401, path
            assert resp.headers["www-authenticate"] == "Bearer"

    def test_shells_serve_tokenless_but_api_stays_gated(self, client):
        settings.WEB_TOKEN = TOKEN
        assert client.get("/").status_code == 200
        assert client.get("/reader").status_code == 200

    def test_patch_rejects_anonymous(self, client):
        settings.WEB_TOKEN = TOKEN
        resp = client.patch("/api/config", json={"COMIC_MIN_MINUTES": 7})
        assert resp.status_code == 401

    def test_bearer_header_grants(self, client):
        settings.WEB_TOKEN = TOKEN
        assert client.get("/api/config", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
        assert client.get("/", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200

    def test_query_token_is_not_accepted_for_http_api(self, client):
        settings.WEB_TOKEN = TOKEN
        assert client.get("/api/config", params={"token": TOKEN}).status_code == 401

    def test_device_comic_reads_stay_tokenless(self, client):
        settings.WEB_TOKEN = TOKEN
        # The route must RUN, not be blocked: /api/comics/current answers from
        # whatever the dev database holds (404 when empty, 200 on a self-hosted
        # box with displayed comics on disk) — never 401. A nonexistent issue
        # is deterministic regardless of database state: 404, never 401.
        assert client.get("/api/comics/current").status_code in (200, 404)
        assert client.get("/api/comics/9999/png").status_code == 404
        assert client.get("/api/comics/9999/bin").status_code == 404

    def test_device_press_report_stays_tokenless(self, client):
        settings.WEB_TOKEN = TOKEN
        # The firmware has no token capability: the route must run (200), not 401.
        resp = client.post("/api/device/buttons", json={"button": "trigger"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_device_press_report_rejects_bad_body_even_tokenless(self, client):
        settings.WEB_TOKEN = TOKEN
        # Open path ≠ unvalidated: an unknown button still gets the route's 400.
        resp = client.post("/api/device/buttons", json={"button": "left"})
        assert resp.status_code == 400

    def test_static_assets_stay_tokenless(self, client):
        settings.WEB_TOKEN = TOKEN
        assert client.get("/static/js/api.js").status_code == 200


class TestEventFeedWebsocket:
    @pytest.fixture
    def fake_dispatcher(self, monkeypatch):
        class Fake:
            def get_status(self):
                return {"is_speaking": False}

        class FakePipeline:
            dispatcher = Fake()

        class FakePipelines:
            def for_device(self, device_id):
                return FakePipeline()

        monkeypatch.setattr(main_module, "pipelines", FakePipelines())

    def test_rejects_without_token(self, fake_dispatcher):
        settings.WEB_TOKEN = TOKEN
        with pytest.raises(WebSocketDisconnect):
            with TestClient(app).websocket_connect("/ws/events"):
                pass

    def test_accepts_query_token(self, fake_dispatcher):
        settings.WEB_TOKEN = TOKEN
        with TestClient(app).websocket_connect(f"/ws/events?token={TOKEN}") as ws:
            snapshot = ws.receive_json()
        assert snapshot["event"] == "INIT_SNAPSHOT"
