"""Secret handling for the remote image API: FLUX_API_KEY masked in GET
/api/config, echoing the mask back to PATCH is a no-op (the stored key survives),
and the image connectivity probe sends the key as a Bearer header when set."""
import httpx
import pytest
from fastapi.testclient import TestClient

from server import config_api, config_test
from server.config import settings
from server.main import app

MASKED = config_api.MASKED


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """PATCH persistence goes to a throwaway .env; settings are restored
    afterwards so other test modules see import-time values."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(config_api, "ENV_FILE", env_file)
    snapshot = settings.model_dump()
    yield env_file
    for name, value in snapshot.items():
        setattr(settings, name, value)


class TestFluxKeyMasking:
    def test_get_masks_flux_api_key(self, client):
        settings.FLUX_API_KEY = "sk-real-secret"
        body = client.get("/api/config").json()
        assert body["values"]["FLUX_API_KEY"] == MASKED
        assert body["fields"]["FLUX_API_KEY"]["secret"] is True

    def test_include_secrets_reveals_flux_api_key(self, client):
        settings.FLUX_API_KEY = "sk-real-secret"
        values = client.get("/api/config", params={"include_secrets": "true"}).json()["values"]
        assert values["FLUX_API_KEY"] == "sk-real-secret"

    def test_masked_echo_is_a_no_op(self, client):
        settings.FLUX_API_KEY = "sk-real-secret"
        resp = client.patch("/api/config", json={"FLUX_API_KEY": MASKED})
        assert resp.status_code == 200
        assert resp.json()["results"] == {}  # nothing applied, nothing dropped
        assert settings.FLUX_API_KEY == "sk-real-secret"

    def test_masked_echo_is_not_persisted_and_other_fields_apply(self, client, isolated):
        settings.FLUX_API_KEY = "sk-real-secret"
        resp = client.patch("/api/config", json={"FLUX_API_KEY": MASKED, "COMIC_MIN_MINUTES": 9})
        assert resp.status_code == 200
        assert resp.json()["results"] == {"COMIC_MIN_MINUTES": "applied"}
        assert settings.FLUX_API_KEY == "sk-real-secret"
        assert "FLUX_API_KEY" not in isolated.read_text()

    def test_real_key_values_still_apply(self, client):
        resp = client.patch("/api/config", json={"FLUX_API_KEY": "sk-new-key"})
        assert resp.status_code == 200
        assert settings.FLUX_API_KEY == "sk-new-key"


class TestImageProbeAuth:
    def test_probe_sends_bearer_header_when_key_set(self, client, monkeypatch):
        settings.FLUX_API_KEY = "sk-probe"
        captured = {}

        async def fake_get(url, headers=None):
            captured["url"], captured["headers"] = url, headers
            return httpx.Response(200, json={"data": [{"id": "m"}]})

        monkeypatch.setattr(config_test, "_http_get", fake_get)
        body = client.post("/api/config/test/image", json={"url": "http://render.local:8000"}).json()
        assert body["ok"] is True
        assert captured["url"] == "http://render.local:8000/v1/models"
        assert captured["headers"] == {"Authorization": "Bearer sk-probe"}

    def test_probe_without_key_sends_no_auth_header(self, client, monkeypatch):
        settings.FLUX_API_KEY = ""
        captured = {}

        async def fake_get(url, headers=None):
            captured["headers"] = headers
            return httpx.Response(200, json={"data": [{"id": "m"}]})

        monkeypatch.setattr(config_test, "_http_get", fake_get)
        body = client.post("/api/config/test/image", json={"url": "http://render.local:8000"}).json()
        assert body["ok"] is True
        assert captured["headers"] is None
