"""
Configuration settings for Comack Server.

A single, validated source of truth built on pydantic-settings: every field
name IS its environment variable name (HOST, LLM_URL, COMIC_MIN_MINUTES, ...),
loaded from the process environment first, then the repo-root `.env` file.
Invalid values fail startup — or a PATCH /api/config — with a readable message
naming the offending variable and value (see format_validation_error).
"""
import logging
import json
import os
from difflib import get_close_matches
from pathlib import Path
from typing import Annotated, ClassVar, Literal

from dotenv import dotenv_values
from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger("comack.config")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
COMICS_DIR = DATA_DIR / "comics"
AUDIO_DIR = DATA_DIR / "audio"
STATIC_DIR = BASE_DIR / "server" / "web" / "static"
PRESETS_DIR = BASE_DIR / "presets"

COMICS_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

ENV_FILE = BASE_DIR / ".env"

# Recognized environment variables that are NOT settings fields.
# HF_HUB_OFFLINE is read by huggingface_hub at model load, not by config.
KNOWN_EXTERNAL_VARS = {"HF_HUB_OFFLINE"}
# Display panel size is fixed hardware spec: present in .env.example for
# reference, deliberately not env-overridable (see Settings.DISPLAY_WIDTH).
FIXED_VARS = {"DISPLAY_WIDTH", "DISPLAY_HEIGHT"}

# Env-var name prefixes that mark a variable as "trying to configure Comack".
# Deliberately excludes HOST/PORT: too generic as prefixes (docker sets
# HOSTNAME in every container), so those two are exact-match only.
COMACK_VAR_PREFIXES = (
    "LLM_", "MINER_", "WHISPER_", "FLUX_", "COMIC_", "SNEAKY_", "SECRET_",
    "POS_", "ARC_", "PROCESS_", "DEFAULT_LINES", "DISPLAY_", "COMACK_",
)


class Settings(BaseSettings):
    """Comack server settings. Field names are the environment variable names."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",  # unrecognized vars are reported by warn_on_unknown_env
    )

    # ── Server network bindings ───────────────────────────────────────────
    HOST: str = Field("0.0.0.0", description="Address the FastAPI server binds to.")
    PORT: int = Field(8000, ge=1, le=65535, description="Port for the FastAPI server (dashboard, WebSocket audio, comic API).")

    # ── OpenAI-compatible LLM ─────────────────────────────────────────────
    LLM_URL: str = Field(
        "", description="OpenAI-compatible chat endpoint (llama.cpp, vLLM, Ollama, OpenRouter...), e.g. http://host:8080/ or https://openrouter.ai/api/v1. Empty disables story mining.")
    LLM_MODEL: str = Field(
        "", description="Model name sent with each request (llama.cpp ignores it; OpenRouter wants e.g. anthropic/claude-3.5-sonnet).")
    LLM_API_KEY: str = Field(
        "", description="Bearer token for the story LLM endpoint (OpenRouter, OpenAI, …). "
        "Sent on every chat call and the connectivity probe when set (masked in GET /api/config). "
        "Leave empty for local servers that do not authenticate.")
    MINER_LLM_URL: str = Field(
        "", description="Separate small, fast server for the per-sentence miner pass (empty = LLM_URL).")
    MINER_LLM_MODEL: str = Field(
        "", description="Model for miner passes (empty = LLM_MODEL). Use a free OpenRouter slug "
        "e.g. openai/gpt-oss-20b:free to keep story on a paid model.")

    # ── Hugging Face ──────────────────────────────────────────────────────
    HF_TOKEN: str = Field(
        "", description="Hugging Face token for gated models (secret: masked in /api/config). Falls back to ~/.cache/huggingface/token.")

    # ── Whisper speech-to-text ────────────────────────────────────────────
    WHISPER_MODEL_SIZE: str = Field(
        "base.en", description="Model size/name passed to faster-whisper (e.g. tiny.en, base.en, small.en). Requires restart.")
    WHISPER_DEVICE: Literal["cuda", "cpu", "auto"] = Field(
        "auto", description="Device for Whisper. Requires restart.")
    WHISPER_MIN_AVG_LOGPROB: float = Field(
        -1.0, le=0, description="Skip transcribed segments with avg logprob below this (hallucination filter).")
    WHISPER_MAX_COMPRESSION: float = Field(
        2.4, ge=0, description="Skip segments whose compression ratio exceeds this (too repetitive).")
    WHISPER_MAX_NO_SPEECH: float = Field(
        0.6, ge=0, le=1, description="Skip segments that are probably not speech (above this) AND low-confidence.")

    # ── Comic cadence & triggers ──────────────────────────────────────────
    SNEAKY_SILENCE_SECONDS: float = Field(
        0, ge=0, le=86400, description="Sneaky mode: seconds of room silence before a new comic is shown (0 = show as soon as rendered).")
    SECRET_PHRASES: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["alpha protocol"],
        description="Spoken phrases that force an immediate refresh (comma-separated, case-insensitive).")
    COMIC_MIN_MINUTES: float = Field(
        60, ge=0, le=10080,
        description="Never generate a new comic sooner than this many minutes after the last (~1/hour target).")
    COMIC_MAX_MINUTES: float = Field(
        720, ge=0, le=10080,
        description="Force a comic after this many minutes even with few room notes (12h default).")
    DEFAULT_LINES_WANTED: int = Field(
        8, ge=1, le=30, description="How many scrambled lines each comic should collect (feeds the LLM prompt).")
    PROCESS_INTERVAL_MINUTES: float = Field(
        2, ge=0.1, le=1440, description="Buffered speech is transcribed, scrambled and mined this often (trigger phrases check continuously).")

    # ── Privacy scramble ──────────────────────────────────────────────────
    POS_SCRAMBLE_RATE: float = Field(
        0.25, ge=0, le=1, description="Fraction of eligible words swapped for same-POS stand-ins before anything is stored or sent to the LLM.")

    # ── Story arcs ────────────────────────────────────────────────────────
    ARC_MIN_PANELS: int = Field(12, ge=1, le=120, description="Narrative arc length lower bound, in panels.")
    ARC_MAX_PANELS: int = Field(60, ge=1, le=120, description="Narrative arc length upper bound, in panels.")

    # ── Dashboard & access ─────────────────────────────────────────────────
    ONBOARDED: bool = Field(
        False, description="Set automatically when the dashboard's first-run wizard completes. Set to false to see it again.")
    WEB_TOKEN: str = Field(
        "", description="Optional bearer token for browser-facing endpoints (dashboard, /api, /ws/events). Empty = open access (assumes a single-user network like a tailnet). Device-critical paths (/ws/audio, comic image/binary reads) always stay open so the hardware keeps working. When set, the dashboard prompts once for the token.")

    # ── Image generation (local diffusers or external API) ────────────────
    FLUX_MODEL_ID: str = Field(
        "stabilityai/sd-turbo", description="Local model (defaults to fast 1-step SD-Turbo; FLUX.1-schnell for quality on GPU).")
    FLUX_DEVICE: Literal["cuda", "mps", "cpu"] = Field(
        "cuda", description="Device for local image generation.")
    FLUX_INFERENCE_STEPS: int = Field(1, ge=1, le=50, description="Diffusion steps per panel (SD-Turbo: 1; FLUX.1-schnell: 4).")
    FLUX_GUIDANCE_SCALE: float = Field(0.0, ge=0, description="Guidance scale for local generation (0 for SD-Turbo/schnell).")
    FLUX_GGUF_URL: str = Field(
        "https://huggingface.co/city96/FLUX.1-schnell-gguf/blob/main/flux1-schnell-Q4_K_S.gguf",
        description="Where to fetch the GGUF-quantized FLUX transformer from (downloaded once, cached).")
    FLUX_MIN_FREE_VRAM_GB: float = Field(
        10.5, ge=0, description="Pause generation (and wait) when free VRAM drops below this, so the LLM can breathe.")
    FLUX_VRAM_WAIT_SECONDS: float = Field(180, ge=0, description="How long to wait for VRAM before giving up on a panel.")
    FLUX_API_URL: str = Field(
        "", description="External image API (OpenAI-compatible /v1/images/generations). "
        "Set = remote-only: panels render on the API and the local diffusers stack is never touched. "
        "Empty = render locally.")
    FLUX_API_KEY: str = Field(
        "", description="Bearer token for the external image API, sent on every remote render "
        "and the connectivity probe while FLUX_API_URL is set (masked in GET /api/config).")
    FLUX_API_MODEL: str = Field(
        "", description="Model name sent to the external image API in remote-only mode "
        "(empty = omit the field, the provider's default model is used).")

    # E-paper display specs: fixed hardware, not env-overridable.
    DISPLAY_WIDTH: ClassVar[int] = 800
    DISPLAY_HEIGHT: ClassVar[int] = 480

    @field_validator("SECRET_PHRASES", mode="before")
    @classmethod
    def _split_secret_phrases(cls, value):
        """Accept the documented comma-separated form ('alpha protocol, blue moon')
        or a JSON list. NoDecode keeps pydantic-settings from demanding JSON in
        the env var — existing deployments set it comma-separated."""
        if isinstance(value, str) and value.strip().startswith("["):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list):
            return [p.strip().lower() for p in value if str(p).strip()]
        return value

    @model_validator(mode="after")
    def _bounds_are_ordered(self):
        """Paired bounds must not be inverted; name both vars in the message."""
        if self.COMIC_MIN_MINUTES > self.COMIC_MAX_MINUTES:
            raise ValueError(
                f"COMIC_MIN_MINUTES ({self.COMIC_MIN_MINUTES}) must not exceed COMIC_MAX_MINUTES ({self.COMIC_MAX_MINUTES})")
        if self.ARC_MIN_PANELS > self.ARC_MAX_PANELS:
            raise ValueError(
                f"ARC_MIN_PANELS ({self.ARC_MIN_PANELS}) must not exceed ARC_MAX_PANELS ({self.ARC_MAX_PANELS})")
        return self


def format_validation_error(error: ValidationError) -> list[str]:
    """One readable line per problem, naming the variable and the rejected value.
    Shared by startup and PATCH /api/config so both fail with the same messages."""
    lines = []
    for err in error.errors():
        name = ".".join(str(part) for part in err.get("loc", ()))
        if name:
            lines.append(f"{name} = {err.get('input')!r}: {err['msg']}")
        else:  # model-level (cross-field) errors already name the vars involved
            lines.append(err["msg"])
    return lines


def _load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as error:
        problems = format_validation_error(error)
        for line in problems:
            logger.critical("Configuration error: %s", line)
        raise SystemExit("Comack configuration is invalid:\n  " + "\n  ".join(problems)) from error


def _known_setting_names() -> set[str]:
    return set(Settings.model_fields) | KNOWN_EXTERNAL_VARS | FIXED_VARS


# Common wrong names that edit-distance alone mis-suggests (FLUX_TOKEN → HF_TOKEN).
COMMON_MISNOMERS = {
    "FLUX_TOKEN": "FLUX_API_KEY",
    "FLUX_KEY": "FLUX_API_KEY",
    "FLUX_API_TOKEN": "FLUX_API_KEY",
    "LLM_KEY": "LLM_API_KEY",
    "LLM_TOKEN": "LLM_API_KEY",
    "LLM_API_TOKEN": "LLM_API_KEY",
    "OPENROUTER_API_KEY": "LLM_API_KEY",
    "OPENROUTER_KEY": "LLM_API_KEY",
}


def suggest_alternative(name: str) -> str | None:
    """Closest known setting name for a typo'd / aliased variable, if any."""
    if name in COMMON_MISNOMERS:
        return COMMON_MISNOMERS[name]
    matches = get_close_matches(name, sorted(_known_setting_names()), n=1)
    return matches[0] if matches else None


def _env_file_vars() -> dict[str, str]:
    if not ENV_FILE.exists():
        return {}
    return {key: value for key, value in dotenv_values(ENV_FILE).items() if key}


def _candidate_env() -> dict[str, str]:
    """Everything that could be configuring us: the .env file overlaid by the
    real process environment."""
    return {**_env_file_vars(), **os.environ}


def find_unknown_env_vars(env: dict[str, str] | None = None) -> list[str]:
    """Env vars that look Comack-related (known prefix pattern or shipped in
    .env.example style) but are not recognized settings — drift like the old
    COMIC_CYCLE_MINUTES. Pure over `env` (default: process env + .env keys)."""
    if env is None:
        env = _candidate_env()
    known = _known_setting_names()
    unknown = set()
    for name in env:
        if name in known:
            continue
        if any(name.startswith(prefix) for prefix in COMACK_VAR_PREFIXES):
            unknown.add(name)
    return sorted(unknown)


def warn_on_unknown_env(env: dict[str, str] | None = None) -> list[str]:
    """Log drift warnings at startup: unknown Comack-looking vars (with a
    suggestion when one is close), and attempts to override fixed display vars.
    Returns the unknown names (testable; called from main's lifespan)."""
    if env is None:
        env = _candidate_env()
    for name in sorted(FIXED_VARS & set(env)):
        logger.info("%s is fixed in code (800x480 panel); not env-overridable — ignoring.", name)
    unknown = find_unknown_env_vars(env)
    for name in unknown:
        suggestion = suggest_alternative(name)
        hint = f" Did you mean {suggestion}?" if suggestion else ""
        logger.warning(
            "Unknown setting %s in the environment: not a recognized Comack "
            "setting (see .env.example).%s", name, hint)
    return unknown


settings = _load_settings()
