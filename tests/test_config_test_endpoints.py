"""POST /api/config/test/{llm,image,whisper}: read-only probes, graceful
degradation when heavy deps are absent (200 + ok:false, never a 500)."""
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from server import config_test
from server.config import settings
from server.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def restore_settings():
    snapshot = settings.model_dump()
    yield
    for name, value in snapshot.items():
        setattr(settings, name, value)


def fake_models_response(models):
    return httpx.Response(200, json={"data": [{"id": m} for m in models]})


class TestLLMProbe:
    def test_not_configured_is_a_finding_not_an_error(self, client, restore_settings):
        settings.LLM_URL = ""
        body = client.post("/api/config/test/llm").json()
        assert resp_ok(body) and body["ok"] is False
        assert body["detail"]["configured"] is False

    def test_reachable_with_model_present(self, client, restore_settings, monkeypatch):
        async def fake_get(url):
            return fake_models_response(["llama-3", "qwen"])

        monkeypatch.setattr(config_test, "_http_get", fake_get)
        body = client.post("/api/config/test/llm", json={"url": "http://llm.local:8080", "model": "llama-3"}).json()
        assert body["ok"] is True
        assert body["detail"]["model_present"] is True
        assert body["detail"]["reachable"] is True

    def test_model_absent_warns_but_stays_reachable(self, client, restore_settings, monkeypatch):
        async def fake_get(url):
            return fake_models_response(["llama-3"])

        monkeypatch.setattr(config_test, "_http_get", fake_get)
        body = client.post("/api/config/test/llm", json={"url": "http://llm.local:8080", "model": "nope"}).json()
        assert body["ok"] is True
        assert body["detail"]["model_present"] is False
        assert "note" in body["detail"]

    def test_unreachable_reports_error(self, client, restore_settings, monkeypatch):
        async def dead(url):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(config_test, "_http_get", dead)
        body = client.post("/api/config/test/llm", json={"url": "http://127.0.0.1:1"}).json()
        assert body["ok"] is False
        assert body["detail"]["reachable"] is False
        assert "error" in body["detail"]

    def test_probe_does_not_mutate_settings(self, client, restore_settings, monkeypatch):
        async def fake_get(url):
            return fake_models_response(["m"])

        monkeypatch.setattr(config_test, "_http_get", fake_get)
        before = settings.model_dump()
        client.post("/api/config/test/llm", json={"url": "http://proposed:8080", "model": "m"})
        assert settings.model_dump() == before


class TestImageProbe:
    def test_local_without_heavy_deps_reports_not_installed(self, client, restore_settings, monkeypatch):
        def no_torch():
            raise ImportError("No module named 'torch'")

        monkeypatch.setattr(config_test, "_import_image_stack", no_torch)
        resp = client.post("/api/config/test/image")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["detail"]["installed"] is False
        assert "not installed" in body["message"]

    def test_local_ready_reports_device(self, client, restore_settings, monkeypatch):
        class FakeTorch:
            class cuda:
                is_available = staticmethod(lambda: False)

        monkeypatch.setattr(config_test, "_import_image_stack", lambda: (FakeTorch, object()))
        settings.FLUX_DEVICE = "cpu"
        body = client.post("/api/config/test/image").json()
        assert body["ok"] is True
        assert body["detail"]["mode"] == "local"
        assert body["detail"]["device_available"] is True

    def test_external_url_reachable(self, client, restore_settings, monkeypatch):
        async def fake_get(url):
            return fake_models_response(["sdxl"])

        monkeypatch.setattr(config_test, "_http_get", fake_get)
        body = client.post("/api/config/test/image", json={"url": "http://render.local:8000"}).json()
        assert body["ok"] is True
        assert body["detail"]["mode"] == "external"


class TestWhisperProbe:
    def test_without_faster_whisper_reports_not_installed(self, client, restore_settings, monkeypatch):
        def no_whisper():
            raise ImportError("No module named 'faster_whisper'")

        monkeypatch.setattr(config_test, "_import_faster_whisper", no_whisper)
        resp = client.post("/api/config/test/whisper")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["detail"]["installed"] is False
        assert "not installed" in body["message"]

    def test_installed_uncached_will_download(self, client, restore_settings, monkeypatch):
        monkeypatch.setattr(config_test, "_import_faster_whisper", lambda: object())
        monkeypatch.setattr(config_test, "_model_cache_dirs", lambda size: [])
        body = client.post("/api/config/test/whisper", json={"model_size": "base.en"}).json()
        assert body["ok"] is True
        assert body["detail"]["cached"] is False

    def test_installed_and_cached(self, client, restore_settings, monkeypatch):
        monkeypatch.setattr(config_test, "_import_faster_whisper", lambda: object())
        monkeypatch.setattr(config_test, "_model_cache_dirs", lambda size: [Path(f"/hub/models--Systran--faster-whisper-{size}")])
        body = client.post("/api/config/test/whisper", json={"model_size": "tiny.en"}).json()
        assert body["ok"] is True
        assert body["detail"]["cached"] is True


def resp_ok(body):
    return {"ok", "message", "detail"} <= set(body)
