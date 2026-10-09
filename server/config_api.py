"""
Runtime configuration API: GET and PATCH /api/config.

GET returns the resolved settings with secret-shaped values masked
(`include_secrets=true` opts in). PATCH validates a JSON object of changes
against the full Settings model — the same validators and readable errors as
startup — then applies runtime-adjustable fields immediately and marks
startup-bound fields `restart_required`. Accepted values are persisted to the
repo-root `.env` so edits survive restart; real environment variables (systemd
unit, docker-compose) still win over `.env`, which the response notes.
"""
import logging
import os
import re
from pathlib import Path
from typing import Any, Literal, get_args, get_origin
from urllib.parse import urlparse, urlunparse

from dotenv import dotenv_values
from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import ValidationError

from server.config import ENV_FILE, PRESETS_DIR, Settings, format_validation_error, settings, suggest_alternative

logger = logging.getLogger("comack.config_api")

router = APIRouter(prefix="/api")

# Fields bound when the process or a cached model starts: the running server
# cannot honor new values, so PATCH marks them restart_required. Everything
# else is read live per call/loop-tick/render and is hot-adjustable. (The FLUX
# pipeline is released after every comic, so FLUX_* settings re-read next render.)
RESTART_REQUIRED_FIELDS = {
    "HOST", "PORT",                          # uvicorn binds once
    "WHISPER_MODEL_SIZE", "WHISPER_DEVICE",  # faster-whisper model cached for process lifetime
    "HF_TOKEN",                              # read at pipeline load; also cached in ~/.cache/huggingface
}

MASKED = "********"
_SECRET_NAME = re.compile(r"(?:API_?KEY|PASSWORD|PASSWD|SECRET|TOKEN)", re.IGNORECASE)


def is_secret_field(name: str) -> bool:
    """Secret-shaped by name: API keys, tokens, passwords — and SECRET_PHRASES,
    which is effectively a spoken passphrase on a privacy-first appliance."""
    return bool(_SECRET_NAME.search(name))


def mask_url_credentials(value: str) -> str:
    """Hide a password embedded in a URL's userinfo (user:pass@host)."""
    parsed = urlparse(value)
    if not parsed.password:
        return value
    host_part = parsed.netloc.rsplit("@", 1)[1]
    user = parsed.username or ""
    return urlunparse(parsed._replace(netloc=f"{user}:{MASKED}@{host_part}"))


def mask_value(name: str, value: Any) -> Any:
    if is_secret_field(name):
        return MASKED
    if isinstance(value, str) and "://" in value:
        return mask_url_credentials(value)
    return value


def format_env_value(value: Any) -> str:
    """Serialize a field value to a single .env line (lists stay comma-separated)."""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def persist_env_updates(updates: dict[str, str], env_path: Path | None = None) -> Path:
    """Upsert KEY=value lines into .env, preserving comments and unrelated lines."""
    path = env_path if env_path is not None else ENV_FILE
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        stripped = line.strip()
        match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=", stripped) if stripped and not stripped.startswith("#") else None
        if match and match.group(1) in remaining:
            out.append(f"{match.group(1)}={remaining.pop(match.group(1))}")
        else:
            out.append(line)
    for name in sorted(remaining):
        out.append(f"{name}={remaining[name]}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def _field_type(info) -> dict[str, Any]:
    if get_origin(info.annotation) is Literal:
        return {"value_type": "enum", "choices": list(get_args(info.annotation))}
    return {"value_type": str(info.annotation).replace("<class '", "").replace("'>", "")}


def _field_metadata() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "description": info.description,
            "runtime_adjustable": name not in RESTART_REQUIRED_FIELDS,
            "secret": is_secret_field(name),
            **_field_type(info),
        }
        for name, info in Settings.model_fields.items()
    }


def _masked_config(include_secrets: bool) -> dict[str, Any]:
    values = settings.model_dump()
    if include_secrets:
        return values
    return {name: mask_value(name, value) for name, value in values.items()}


@router.get("/config")
async def get_config(include_secrets: bool = Query(False, description="Reveal secret-shaped values (API keys, tokens).")):
    return {"values": _masked_config(include_secrets), "fields": _field_metadata(), "masked": not include_secrets}


@router.get("/config/presets")
async def get_presets():
    """The shipped hardware-profile presets (local-gpu, external-api, cpu-only)
    as raw value dicts, for the onboarding wizard's backend-choice prefill."""
    presets = []
    if PRESETS_DIR.is_dir():
        for path in sorted(PRESETS_DIR.glob("*.env")):
            presets.append({"name": path.stem, "values": {k: v for k, v in dotenv_values(path).items() if k}})
    return {"presets": presets}


@router.patch("/config")
async def patch_config(updates: dict[str, Any] = Body(..., description="Object of setting name -> new value.")):
    """Validate against the full model, apply hot fields, persist everything."""
    if not updates:
        raise HTTPException(status_code=400, detail="No settings provided. Send an object like {\"COMIC_MIN_MINUTES\": 10}.")

    if unknown := [name for name in updates if name not in Settings.model_fields]:
        hints = [f"{name} is not a recognized setting" + (f" — did you mean {s}?" if (s := suggest_alternative(name)) else "")
                 for name in unknown]
        raise HTTPException(status_code=400, detail={"errors": hints})

    # The dashboard round-trips GET's masked values, so a secret field sent back as the
    # mask ("********") means "unchanged": drop it and keep the stored value instead of
    # persisting the literal mask over the real key. (The settings UI sends only changed
    # fields; this guards every other client of the same API contract.)
    changes = {name: value for name, value in updates.items()
               if not (is_secret_field(name) and value == MASKED)}

    merged = settings.model_dump() | changes
    try:  # full-model validation: same bounds, enums and cross-checks as startup
        candidate = Settings(**merged)
    except ValidationError as error:
        raise HTTPException(status_code=400, detail={"errors": format_validation_error(error)}) from error

    results: dict[str, str] = {}
    for name in changes:
        setattr(settings, name, getattr(candidate, name))
        results[name] = "restart_required" if name in RESTART_REQUIRED_FIELDS else "applied"

    notes = []
    try:
        persist_env_updates({name: format_env_value(getattr(candidate, name)) for name in changes})
    except OSError as error:
        logger.error("Could not persist settings to %s: %s", ENV_FILE, error)
        notes.append(f"Settings applied for this run, but persisting to {ENV_FILE} failed: {error}")

    for name in changes:
        if name in os.environ:
            notes.append(
                f"{name} is also set in the process environment (systemd unit or docker-compose); "
                "that value wins over .env on restart — update the unit/compose file too.")

    if restarts := [name for name, status in results.items() if status == "restart_required"]:
        logger.info("Config patched (restart required for %s).", ", ".join(restarts))
    else:
        logger.info("Config patched: %s.", ", ".join(changes))
    return {"results": results, "notes": notes, "config": _masked_config(include_secrets=False)}
