"""Remote-only panel generation over an OpenAI-compatible image API.

All HTTP runs through a mocked transport — CI has no network and no torch.
Covers the contract the spec pins: the llm.api_url URL join, the OpenAI payload
shape, Bearer auth, b64_json/url response parsing, remote-only exhaustion that
never touches the local pipeline, retry pacing, and prompt-log visibility.
"""
import base64
import io
import json
from unittest.mock import patch

import httpx
import pytest
from PIL import Image

from server import llm
from server.config import settings
from server.render import flux_generator as fg


@pytest.fixture
def restore_settings():
    snapshot = settings.model_dump()
    yield
    for name, value in snapshot.items():
        setattr(settings, name, value)


def _png_bytes(width=4, height=4):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (12, 34, 56)).save(buf, format="PNG")
    return buf.getvalue()


def _b64_response(width=4, height=4):
    b64 = base64.b64encode(_png_bytes(width, height)).decode()
    return httpx.Response(200, json={"data": [{"b64_json": b64}]})


def _mock_http(monkeypatch, handler, captured):
    """Swap httpx.Client for one backed by a MockTransport; every request is
    appended to `captured` before the handler answers it."""
    real_client = httpx.Client

    def factory(**kwargs):
        def route(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return handler(request)

        return real_client(transport=httpx.MockTransport(route), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


class TestRemoteContract:
    def test_url_join_is_v1_aware(self, restore_settings, monkeypatch):
        captured = []
        _mock_http(monkeypatch, lambda req: _b64_response(), captured)
        for base in ("http://gpu-box:8000", "http://gpu-box:8000/",
                     "http://gpu-box:8000/v1", "http://gpu-box:8000/v1/"):
            captured.clear()
            settings.FLUX_API_URL = base
            fg.flux_generator._generate_via_api("comic panel: A cat", (380, 420))
            assert str(captured[0].url) == "http://gpu-box:8000/v1/images/generations", base

    def test_payload_is_openai_shaped(self, restore_settings, monkeypatch):
        settings.FLUX_API_URL = "http://gpu-box:8000"
        settings.FLUX_API_MODEL = ""
        captured = []
        _mock_http(monkeypatch, lambda req: _b64_response(), captured)
        img = fg.flux_generator.generate_panel_art("A cat astronaut", (380, 420))
        body = json.loads(captured[0].content)
        assert body == {
            "prompt": "comic panel: A cat astronaut",  # prefix matches the local path
            "n": 1,
            "size": "380x420",
            "response_format": "b64_json",
        }
        assert "model" not in body
        assert isinstance(img, Image.Image) and img.mode == "RGB"

    def test_payload_carries_model_when_configured(self, restore_settings, monkeypatch):
        settings.FLUX_API_URL = "http://gpu-box:8000"
        settings.FLUX_API_MODEL = "flux-schnell"
        captured = []
        _mock_http(monkeypatch, lambda req: _b64_response(), captured)
        fg.flux_generator._generate_via_api("comic panel: A cat", (380, 420))
        assert json.loads(captured[0].content)["model"] == "flux-schnell"

    def test_bearer_header_sent_when_key_set(self, restore_settings, monkeypatch):
        settings.FLUX_API_URL = "http://gpu-box:8000"
        settings.FLUX_API_KEY = "sk-secret"
        captured = []
        _mock_http(monkeypatch, lambda req: _b64_response(), captured)
        fg.flux_generator._generate_via_api("comic panel: A cat", (380, 420))
        assert captured[0].headers["authorization"] == "Bearer sk-secret"

    def test_no_auth_header_without_key(self, restore_settings, monkeypatch):
        settings.FLUX_API_URL = "http://gpu-box:8000"
        settings.FLUX_API_KEY = ""
        captured = []
        _mock_http(monkeypatch, lambda req: _b64_response(), captured)
        fg.flux_generator._generate_via_api("comic panel: A cat", (380, 420))
        assert "authorization" not in captured[0].headers

    def test_b64_response_parses_to_rgb_image(self, restore_settings, monkeypatch):
        settings.FLUX_API_URL = "http://gpu-box:8000"
        captured = []
        _mock_http(monkeypatch, lambda req: _b64_response(4, 6), captured)
        img = fg.flux_generator.generate_panel_art("A cat", (380, 420))
        assert isinstance(img, Image.Image) and img.mode == "RGB" and img.size == (4, 6)

    def test_url_shaped_response_is_fetched(self, restore_settings, monkeypatch):
        # A provider that ignored response_format answered with a URL: the bytes
        # are fetched instead of a KeyError being swallowed into procedural art.
        settings.FLUX_API_URL = "http://gpu-box:8000"
        png = _png_bytes(5, 7)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(200, json={"data": [{"url": "http://provider:9000/img.png"}]})
            return httpx.Response(200, content=png)

        captured = []
        _mock_http(monkeypatch, handler, captured)
        img = fg.flux_generator.generate_panel_art("A cat", (380, 420))
        assert isinstance(img, Image.Image) and img.size == (5, 7)
        assert [r.method for r in captured] == ["POST", "GET"]

    def test_response_without_image_fields_raises(self, restore_settings, monkeypatch):
        settings.FLUX_API_URL = "http://gpu-box:8000"
        _mock_http(monkeypatch, lambda req: httpx.Response(200, json={"data": [{}]}), [])
        with pytest.raises(ValueError, match="no b64_json or url"):
            fg.flux_generator._generate_via_api("comic panel: A cat", (380, 420))

    def test_http_error_status_propagates(self, restore_settings, monkeypatch):
        # Errors propagate to the retry loop — no silent None, no fall-through.
        settings.FLUX_API_URL = "http://gpu-box:8000"
        _mock_http(monkeypatch, lambda req: httpx.Response(500, text="boom"), [])
        with pytest.raises(httpx.HTTPStatusError):
            fg.flux_generator._generate_via_api("comic panel: A cat", (380, 420))


class TestRemoteOnlySemantics:
    def test_remote_success_never_touches_local_pipeline(self, restore_settings, monkeypatch):
        settings.FLUX_API_URL = "http://gpu-box:8000"
        captured = []
        _mock_http(monkeypatch, lambda req: _b64_response(), captured)
        with patch.object(fg, "_load_flux_pipeline") as load, \
             patch.object(fg.FluxGenerator, "_generate_local") as local:
            img = fg.flux_generator.generate_panel_art("A cat", (380, 420))
        load.assert_not_called()
        local.assert_not_called()
        assert isinstance(img, Image.Image)

    def test_remote_exhaustion_raises_panel_art_failed(self, restore_settings, monkeypatch):
        settings.FLUX_API_URL = "http://gpu-box:8000"
        attempts = []

        def dead_endpoint(*_):
            attempts.append(1)
            raise httpx.ConnectError("connection refused")

        sleeps = []
        with patch.object(fg.FluxGenerator, "_generate_via_api", side_effect=dead_endpoint), \
             patch.object(fg, "_load_flux_pipeline") as load, \
             patch.object(fg.FluxGenerator, "_generate_local_loop") as local_loop, \
             patch.object(fg.time, "sleep", sleeps.append):
            with pytest.raises(fg.PanelArtFailed, match="remote generation failed after"):
                fg.flux_generator.generate_panel_art("A cat", (380, 420))
        assert len(attempts) == len(fg.RETRY_DELAYS_S) + 1
        assert sleeps == list(fg.RETRY_DELAYS_S)
        load.assert_not_called()
        local_loop.assert_not_called()

    def test_remote_retry_pacing_between_attempts(self, restore_settings, monkeypatch):
        settings.FLUX_API_URL = "http://gpu-box:8000"
        img = Image.new("RGB", (4, 4))
        outcomes = [httpx.ConnectError("a"), httpx.ConnectError("b"), img]

        def flaky(*_):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        sleeps = []
        with patch.object(fg.FluxGenerator, "_generate_via_api", side_effect=flaky), \
             patch.object(fg.time, "sleep", sleeps.append):
            result = fg.flux_generator.generate_panel_art("A cat", (380, 420))
        assert result is img
        assert sleeps == list(fg.RETRY_DELAYS_S[:2])


class TestPromptLog:
    def test_remote_success_is_visible_in_prompt_log(self, restore_settings, monkeypatch):
        settings.FLUX_API_URL = "http://gpu-box:8000"
        settings.FLUX_API_MODEL = "flux-schnell"
        llm.prompt_log.clear()
        captured = []
        _mock_http(monkeypatch, lambda req: _b64_response(6, 5), captured)
        fg.flux_generator.generate_panel_art("A cat", (380, 420))
        entry = llm.prompt_log[-1]
        assert entry["model"] == "flux-schnell"
        assert entry["prompt"] == "comic panel: A cat"
        assert entry["output"] == "[remote image 6x5]"
