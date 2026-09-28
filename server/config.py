"""
Configuration settings for Comack Server.
Supports loading from environment variables and .env file.
"""
import os
from pathlib import Path
try:
    from pydantic import BaseModel
except ImportError:
    class BaseModel:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass
DATA_DIR = BASE_DIR / "data"
COMICS_DIR = DATA_DIR / "comics"
AUDIO_DIR = DATA_DIR / "audio"
STATIC_DIR = BASE_DIR / "server" / "web" / "static"

COMICS_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseModel):
    # Server network bindings
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    
    # llama.cpp server configuration
    LLAMA_CPP_URL: str = os.getenv("LLAMA_CPP_URL", "http://comack-server.example-tailnet.ts.net:80/")
    LLAMA_CPP_MODEL: str = os.getenv("LLAMA_CPP_MODEL", "")
    # Small, fast model for the per-sentence miner pass (empty = use LLAMA_CPP_URL)
    MINER_LLAMA_URL: str = os.getenv("MINER_LLAMA_URL", "")
    
    # Whisper Speech-to-Text configuration
    WHISPER_MODEL_SIZE: str = os.getenv("WHISPER_MODEL_SIZE", "base.en")
    WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "auto")  # 'cuda', 'cpu', or 'auto'
    # A transcribed segment is skipped as hallucinated (Whisper's own thresholds) when it is too
    # repetitive, or probably not speech AND low-confidence.
    WHISPER_MIN_AVG_LOGPROB: float = float(os.getenv("WHISPER_MIN_AVG_LOGPROB", "-1.0"))
    WHISPER_MAX_COMPRESSION: float = float(os.getenv("WHISPER_MAX_COMPRESSION", "2.4"))
    WHISPER_MAX_NO_SPEECH: float = float(os.getenv("WHISPER_MAX_NO_SPEECH", "0.6"))
    
    # Sneaky Comic Mode: Strict silence threshold in seconds (45 to 60s)
    SNEAKY_SILENCE_SECONDS: float = float(os.getenv("SNEAKY_SILENCE_SECONDS", "50.0"))
    
    # Spoken phrases that force an immediate refresh (comma-separated)
    SECRET_PHRASES: list[str] = [p.strip().lower() for p in os.getenv("SECRET_PHRASES", "alpha protocol").split(",") if p.strip()]

    # A new comic is generated once the room notes the last comic asked for have arrived,
    # but never sooner than COMIC_MIN_MINUTES and never later than COMIC_MAX_MINUTES.
    COMIC_MIN_MINUTES: float = float(os.getenv("COMIC_MIN_MINUTES", "5"))
    COMIC_MAX_MINUTES: float = float(os.getenv("COMIC_MAX_MINUTES", "1440"))  # 1 day
    DEFAULT_LINES_WANTED: int = int(os.getenv("DEFAULT_LINES_WANTED", "8"))

    # Buffered speech is transcribed, scrambled and mined this often (trigger phrases are
    # checked continuously on a rolling few-second window instead)
    PROCESS_INTERVAL_MINUTES: float = float(os.getenv("PROCESS_INTERVAL_MINUTES", "2"))

    # Stochastic Part-of-Speech "Differential Privacy" word scramble rate
    POS_SCRAMBLE_RATE: float = float(os.getenv("POS_SCRAMBLE_RATE", "0.25"))
    
    # Narrative arc length bounds, in panels
    ARC_MIN_PANELS: int = int(os.getenv("ARC_MIN_PANELS", "12"))
    ARC_MAX_PANELS: int = int(os.getenv("ARC_MAX_PANELS", "60"))
    
    # Image Generation Configuration (Defaults to fast 1-step SD-Turbo for immediate responsiveness)
    FLUX_MODEL_ID: str = os.getenv("FLUX_MODEL_ID", "stabilityai/sd-turbo")
    FLUX_DEVICE: str = os.getenv("FLUX_DEVICE", "cuda")  # 'cuda', 'mps', or 'cpu'
    FLUX_INFERENCE_STEPS: int = int(os.getenv("FLUX_INFERENCE_STEPS", "1"))
    FLUX_GUIDANCE_SCALE: float = float(os.getenv("FLUX_GUIDANCE_SCALE", "0.0"))
    FLUX_GGUF_URL: str = os.getenv(
        "FLUX_GGUF_URL", "https://huggingface.co/city96/FLUX.1-schnell-gguf/blob/main/flux1-schnell-Q4_K_S.gguf")
    FLUX_MIN_FREE_VRAM_GB: float = float(os.getenv("FLUX_MIN_FREE_VRAM_GB", "10.5"))
    FLUX_VRAM_WAIT_SECONDS: float = float(os.getenv("FLUX_VRAM_WAIT_SECONDS", "180"))
    FLUX_API_URL: str = os.getenv("FLUX_API_URL", "") # Optional ComfyUI/external endpoint
    
    # E-paper display specs
    DISPLAY_WIDTH: int = 800
    DISPLAY_HEIGHT: int = 480
    
    # Optional OpenRouter fallback if configured
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")


settings = Settings()
