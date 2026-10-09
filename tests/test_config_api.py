"""GET/PATCH /api/config: masking, apply-status semantics, .env persistence."""
import pytest
from fastapi.testclient import TestClient

from server import config_api
from server.config import settings
from server.main import app

MASKED = config_api.MASKED


@pytest.fixture
def client():
    # No lifespan: the config endpoints need only the settings singleton,
    # not the dispatcher/transcriber stack.
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


def test_get_masks_secret_fields(client):
    settings.HF_TOKEN = "hf_secret_token"
    body = client.get("/api/config").json()
    assert body["masked"] is True
    assert body["values"]["HF_TOKEN"] == MASKED
    assert body["values"]["SECRET_PHRASES"] == MASKED  # a spoken passphrase is secret-shaped
    assert body["fields"]["HF_TOKEN"]["secret"] is True
    assert body["fields"]["COMIC_MIN_MINUTES"]["secret"] is False


def test_get_masks_url_credentials(client):
    settings.LLM_URL = "http://user:hunter2@llm.local:8080/"
    values = client.get("/api/config").json()["values"]
    assert values["LLM_URL"] == "http://user:********@llm.local:8080/"
    revealed = client.get("/api/config", params={"include_secrets": "true"}).json()
    assert revealed["values"]["LLM_URL"] == "http://user:hunter2@llm.local:8080/"
    assert revealed["masked"] is False


def test_include_secrets_reveals(client):
    settings.HF_TOKEN = "hf_secret_token"
    values = client.get("/api/config", params={"include_secrets": "true"}).json()["values"]
    assert values["HF_TOKEN"] == "hf_secret_token"


def test_field_metadata_describes_editability(client):
    fields = client.get("/api/config").json()["fields"]
    assert fields["COMIC_MIN_MINUTES"]["runtime_adjustable"] is True
    assert fields["WHISPER_MODEL_SIZE"]["runtime_adjustable"] is False
    assert fields["WHISPER_DEVICE"]["choices"] == ["cuda", "cpu", "auto"]
    assert fields["HOST"]["description"]


def test_patch_applies_hot_field_and_persists(client, isolated):
    resp = client.patch("/api/config", json={"COMIC_MIN_MINUTES": 7})
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == {"COMIC_MIN_MINUTES": "applied"}
    assert settings.COMIC_MIN_MINUTES == 7.0
    assert body["config"]["COMIC_MIN_MINUTES"] == 7.0
    assert "COMIC_MIN_MINUTES=7.0" in isolated.read_text()


def test_patch_mixed_statuses(client, isolated):
    resp = client.patch("/api/config", json={"WHISPER_DEVICE": "cpu", "SNEAKY_SILENCE_SECONDS": 45})
    body = resp.json()
    assert body["results"] == {"WHISPER_DEVICE": "restart_required", "SNEAKY_SILENCE_SECONDS": "applied"}
    assert settings.WHISPER_DEVICE == "cpu"      # persisted for the restart
    assert settings.SNEAKY_SILENCE_SECONDS == 45.0
    assert "WHISPER_DEVICE=cpu" in isolated.read_text()


def test_patch_rejects_invalid_value_and_changes_nothing(client, isolated):
    before = settings.model_dump()
    resp = client.patch("/api/config", json={"COMIC_MIN_MINUTES": -5})
    assert resp.status_code == 400
    (error,) = resp.json()["detail"]["errors"]
    assert "COMIC_MIN_MINUTES" in error and "-5" in error
    assert settings.model_dump() == before
    assert not isolated.exists()  # nothing persisted on rejection


def test_patch_rejects_inverted_bounds(client):
    resp = client.patch("/api/config", json={"ARC_MIN_PANELS": 30, "ARC_MAX_PANELS": 10})
    assert resp.status_code == 400
    (error,) = resp.json()["detail"]["errors"]
    assert "ARC_MIN_PANELS" in error and "ARC_MAX_PANELS" in error


def test_patch_rejects_unknown_setting_with_suggestion(client):
    resp = client.patch("/api/config", json={"COMIC_CYCLE_MINUTES": 5})
    assert resp.status_code == 400
    (error,) = resp.json()["detail"]["errors"]
    assert "COMIC_CYCLE_MINUTES" in error and "COMIC_MIN_MINUTES" in error

    resp = client.patch("/api/config", json={"NOT_A_COMACK_VAR": 1})
    assert resp.status_code == 400


def test_patch_empty_body_rejected(client):
    assert client.patch("/api/config", json={}).status_code == 400


def test_patch_persists_preserving_comments(client, isolated):
    isolated.write_text("# my hand-written notes\nLLM_URL=http://old:8080/\n\nPORT=8000\n")
    resp = client.patch("/api/config", json={"LLM_URL": "http://new:8080/"})
    assert resp.status_code == 200
    content = isolated.read_text()
    assert "# my hand-written notes" in content
    assert "LLM_URL=http://new:8080/" in content
    assert "PORT=8000" in content


def test_patch_list_values_persist_comma_separated(client, isolated):
    resp = client.patch("/api/config", json={"SECRET_PHRASES": ["blue moon", "open sesame"]})
    assert resp.status_code == 200
    assert settings.SECRET_PHRASES == ["blue moon", "open sesame"]
    assert "SECRET_PHRASES=blue moon, open sesame" in isolated.read_text()
    assert client.get("/api/config").json()["values"]["SECRET_PHRASES"] == MASKED


def test_patch_notes_env_shadowed_values(client, isolated, monkeypatch):
    monkeypatch.setenv("WHISPER_DEVICE", "cuda")
    body = client.patch("/api/config", json={"WHISPER_DEVICE": "cpu"}).json()
    assert any("WHISPER_DEVICE" in note and "environment" in note for note in body["notes"])


def test_onboarding_flag_applies_and_persists(client, isolated):
    assert client.get("/api/config").json()["values"]["ONBOARDED"] is False
    resp = client.patch("/api/config", json={"ONBOARDED": True})
    assert resp.status_code == 200
    assert resp.json()["results"] == {"ONBOARDED": "applied"}  # hot field: no restart needed
    assert settings.ONBOARDED is True
    assert "ONBOARDED=True" in isolated.read_text()


def test_onboarding_flag_is_not_masked_as_secret(client):
    body = client.get("/api/config").json()
    assert body["values"]["ONBOARDED"] is False
    assert body["fields"]["ONBOARDED"]["secret"] is False


def test_presets_endpoint_lists_shipped_profiles(client):
    body = client.get("/api/config/presets").json()
    by_name = {p["name"]: p["values"] for p in body["presets"]}
    assert set(by_name) == {"cpu-only", "external-api", "local-gpu"}
    assert by_name["local-gpu"]["FLUX_DEVICE"] == "cuda"
    assert by_name["external-api"]["FLUX_API_URL"]
    assert by_name["cpu-only"]["WHISPER_DEVICE"] == "cpu"
