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
    
    # Whisper Speech-to-Text configuration
    WHISPER_MODEL_SIZE: str = os.getenv("WHISPER_MODEL_SIZE", "base.en")
    WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "auto")  # 'cuda', 'cpu', or 'auto'
    
    # Sneaky Comic Mode: Strict silence threshold in seconds (45 to 60s)
    SNEAKY_SILENCE_SECONDS: float = float(os.getenv("SNEAKY_SILENCE_SECONDS", "50.0"))
    
    # Comic generation interval in minutes (default 30 min)
    COMIC_CYCLE_MINUTES: int = int(os.getenv("COMIC_CYCLE_MINUTES", "30"))
    
    # Stochastic Part-of-Speech "Differential Privacy" word scramble rate
    POS_SCRAMBLE_RATE: float = float(os.getenv("POS_SCRAMBLE_RATE", "0.25"))
    
    # Narrative arc duration bounds (between 4 hours and 7 days)
    ARC_MIN_HOURS: float = float(os.getenv("ARC_MIN_HOURS", "4.0"))
    ARC_MAX_HOURS: float = float(os.getenv("ARC_MAX_HOURS", "168.0"))  # 7 days
    
    # Image Generation Configuration (Defaults to fast 1-step SD-Turbo for immediate responsiveness)
    FLUX_MODEL_ID: str = os.getenv("FLUX_MODEL_ID", "stabilityai/sd-turbo")
    FLUX_DEVICE: str = os.getenv("FLUX_DEVICE", "cuda")  # 'cuda', 'mps', or 'cpu'
    FLUX_INFERENCE_STEPS: int = int(os.getenv("FLUX_INFERENCE_STEPS", "1"))
    FLUX_GUIDANCE_SCALE: float = float(os.getenv("FLUX_GUIDANCE_SCALE", "0.0"))
    FLUX_ENABLE_CPU_OFFLOAD: bool = os.getenv("FLUX_ENABLE_CPU_OFFLOAD", "false").lower() == "true"
    FLUX_API_URL: str = os.getenv("FLUX_API_URL", "") # Optional ComfyUI/external endpoint
    
    # E-paper display specs
    DISPLAY_WIDTH: int = 800
    DISPLAY_HEIGHT: int = 480
    
    # Optional OpenRouter fallback if configured
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")


settings = Settings()
