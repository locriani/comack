"""
Connection tests behind POST /api/config/test/{llm,image,whisper}.

Probes are read-only: they report whether the configured (or proposed) LLM
endpoint, image backend, or Whisper model is usable right now — they never
mutate settings and never download models. Every probe answers 200 with
{"ok": ..., "message": ..., "detail": ...}; an absent heavy dependency is a
finding ("not installed"), not a 500, so light installs and CI stay meaningful.
Request bodies may carry proposed values ({url, model}, ...) so onboarding can
validate what the user typed before anything is saved; empty bodies test the
current configuration.
"""
import logging
from pathlib import Path
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Body

from server import llm
from server.config import settings

logger = logging.getLogger("comack.config_test")

router = APIRouter(prefix="/api/config/test")

PROBE_TIMEOUT_S = 5.0

# Per-request override keys and the settings fields they shadow, so a probe
# either uses what was typed in the UI or the current configuration.
LLM_OVERRIDES = {"url": "LLM_URL", "model": "LLM_MODEL"}
IMAGE_OVERRIDES = {"url": "FLUX_API_URL", "model": "FLUX_MODEL_ID"}
WHISPER_OVERRIDES = {"model_size": "WHISPER_MODEL_SIZE"}


def _resolve(overrides: Dict[str, Any], mapping: Dict[str, str]) -> Dict[str, str]:
    """Probe inputs: request overrides that are actually set, else current settings."""
    return {
        key: str(overrides[key]) if key in overrides else getattr(settings, field)
        for key, field in mapping.items()
    }


async def _http_get(url: str, headers: Dict[str, str] | None = None) -> httpx.Response:
    """Network seam for tests: the only place a probe touches the network."""
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as client:
        return await client.get(url, headers=headers)


def _import_image_stack():
    """Heavy-dep seam: returns (torch, diffusers) or raises ImportError."""
    import torch
    import diffusers  # noqa: F401 — presence is the point, not the symbols

    return torch, diffusers


def _import_faster_whisper():
    """Heavy-dep seam: returns the faster_whisper module or raises ImportError."""
    import faster_whisper

    return faster_whisper


def _model_cache_dirs(size: str) -> list[Path]:
    """Cached faster-whisper model dirs for `size`, without any network call."""
    cache = Path.home() / ".cache" / "huggingface" / "hub"
    if not cache.exists():
        return []
    return sorted(cache.glob(f"models--*faster-whisper-{size}"))


def _find(resp: httpx.Response, result: Dict[str, Any]) -> Dict[str, Any]:
    """Fill a probe result from a models-list response (external LLM/image API)."""
    result["detail"]["status_code"] = resp.status_code
    if resp.status_code >= 400:
        result["detail"]["reachable"] = True
        result["message"] = f"Endpoint answered, but /v1/models returned HTTP {resp.status_code}."
        return result
    models = None
    try:  # OpenAI shape: {"data": [{"id": ...}, ...]}; anything else is still "reachable"
        models = [m.get("id") for m in resp.json().get("data", []) if isinstance(m, dict)]
    except Exception:
        pass
    result["detail"]["reachable"] = True
    result["ok"] = True
    result["message"] = f"Reachable; {len(models)} model(s) listed." if models is not None else "Reachable."
    if models is not None:
        result["detail"]["models"] = models
    return result


def _unreachable(result: Dict[str, Any], error: Exception) -> Dict[str, Any]:
    result["detail"]["reachable"] = False
    result["detail"]["error"] = repr(error)
    result["message"] = "Could not reach the endpoint."
    return result


@router.post("/llm")
async def test_llm(overrides: Dict[str, Any] = Body(default={})):
    """Probe an OpenAI-compatible chat endpoint: reachability plus, when the
    server lists models, whether the configured model is among them."""
    resolved = _resolve(overrides, LLM_OVERRIDES)
    url, model = resolved["url"].strip(), resolved["model"].strip()
    result: Dict[str, Any] = {"ok": False, "message": "", "detail": {"url": url, "model": model}}
    if not url:
        result["message"] = "No LLM endpoint configured — story mining is disabled until one is set."
        result["detail"]["configured"] = False
        return result
    result["detail"]["configured"] = True
    try:
        resp = await _http_get(llm.api_url(url, "models"))
        result = _find(resp, result)
        models = result["detail"].get("models")
        if model and models is not None:
            present = model in models
            result["detail"]["model_present"] = present
            if present:
                result["message"] = f"Reachable; model '{model}' is available."
            else:  # llama.cpp-style servers ignore the model name, so warn rather than fail
                result["detail"]["note"] = "Model not in the endpoint's list — servers like llama.cpp ignore the name; verify it."
    except Exception as error:
        result = _unreachable(result, error)
    return result


def _local_image_detail() -> Dict[str, Any]:
    """What a local render needs: torch/diffusers importable, the configured
    device actually present, and (on CUDA) enough free VRAM. Raises ImportError
    when the heavy stack is absent — callers turn that into a finding."""
    torch, _ = _import_image_stack()
    device = settings.FLUX_DEVICE
    detail: Dict[str, Any] = {
        "mode": "local", "installed": True, "device": device,
        "model": settings.FLUX_MODEL_ID,
    }
    if device == "cuda":
        detail["device_available"] = torch.cuda.is_available()
        if detail["device_available"]:
            detail["free_vram_gb"] = round(torch.cuda.mem_get_info()[0] / 1e9, 1)
    elif device == "mps":
        detail["device_available"] = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    else:
        detail["device_available"] = True
    return detail


@router.post("/image")
async def test_image(overrides: Dict[str, Any] = Body(default={})):
    """Probe the image backend: an external API's reachability (authenticated
    when FLUX_API_KEY is set), or the local diffusion stack's install state,
    device and free VRAM. Never loads a pipeline, never generates an image."""
    resolved = _resolve(overrides, IMAGE_OVERRIDES)
    url = resolved["url"].strip()
    if url:
        result: Dict[str, Any] = {"ok": False, "message": "", "detail": {"mode": "external", "url": url}}
        probe_url = llm.api_url(url, "models")
        try:
            if settings.FLUX_API_KEY:
                # The key rides as a Bearer header exactly as a remote render would send it;
                # this stays a reachability check, never a paid generation.
                resp = await _http_get(probe_url, {"Authorization": f"Bearer {settings.FLUX_API_KEY}"})
            else:
                resp = await _http_get(probe_url)
            return _find(resp, result)
        except Exception as error:
            return _unreachable(result, error)
    result = {"ok": False, "message": "", "detail": {}}
    try:
        detail = _local_image_detail()
    except ImportError:
        detail = {
            "mode": "local", "installed": False,
            "message": "torch/diffusers not installed — install the comack-server-ai extra, or set FLUX_API_URL to render on an external image API.",
        }
    except Exception as error:  # a broken CUDA install etc. is a finding, not a 500
        logger.warning("Local image probe failed: %s", error)
        detail = {"mode": "local", "installed": False, "message": f"Local image stack unavailable: {error!r}"}
    result["detail"] = detail
    result["ok"] = bool(detail.get("installed") and detail.get("device_available"))
    if result["ok"]:
        result["message"] = f"Local rendering ready on {detail['device']}" + (
            f" ({detail['free_vram_gb']} GB VRAM free)." if "free_vram_gb" in detail else ".")
    elif not result["detail"].get("installed"):
        result["message"] = detail.get("message", "Local image stack not installed.")
    else:
        result["message"] = f"torch/diffusers installed, but the {detail['device']} device is not available."
    return result


@router.post("/whisper")
async def test_whisper(overrides: Dict[str, Any] = Body(default={})):
    """Probe the transcription stack: faster-whisper presence and whether the
    configured model size is already in the local Hugging Face cache. Loading
    the model itself is left to the server's own startup (a large download is
    not something a UI button should trigger)."""
    size = _resolve(overrides, WHISPER_OVERRIDES)["model_size"].strip()
    result: Dict[str, Any] = {"ok": False, "message": "", "detail": {"model_size": size, "device": settings.WHISPER_DEVICE}}
    try:
        _import_faster_whisper()
        result["detail"]["installed"] = True
    except ImportError:
        result["detail"]["installed"] = False
        result["message"] = (
            "faster-whisper not installed — transcription falls back to the offline mock. "
            "Install the comack-server-ai extra for real transcription.")
        return result
    except Exception as error:
        result["detail"]["installed"] = False
        result["message"] = f"faster-whisper is installed but unusable: {error!r}"
        return result
    cached = [str(path.name) for path in _model_cache_dirs(size)]
    result["detail"]["cached"] = bool(cached)
    result["ok"] = True
    result["message"] = (
        f"faster-whisper installed; '{size}' is cached locally and loads at the next restart."
        if cached else
        f"faster-whisper installed; '{size}' will download from Hugging Face on first use.")
    if cached:
        result["detail"]["cache_dirs"] = cached
    return result
