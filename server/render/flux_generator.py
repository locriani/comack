"""
FLUX.1 [schnell] Comic Art Generator.
Exclusively uses black-forest-labs/FLUX.1-schnell for state-of-the-art
high-contrast graphic novel panel illustrations.
"""
import io
import os
import logging
from typing import Optional, Tuple
from pathlib import Path
from PIL import Image

from server.config import settings

logger = logging.getLogger("comack.flux")

_pipeline = None
_pipeline_failed = False


def _load_flux_pipeline():
    """Load the official black-forest-labs/FLUX.1-schnell pipeline."""
    global _pipeline, _pipeline_failed
    if _pipeline is not None or _pipeline_failed:
        return _pipeline
        
    try:
        import torch
        from diffusers import FluxPipeline
        
        token = os.getenv("HF_TOKEN")
        if not token:
            token_path = Path.home() / ".cache" / "huggingface" / "token"
            if token_path.exists():
                token = token_path.read_text().strip()
                
        logger.info(f"Loading official FLUX.1 model: '{settings.FLUX_MODEL_ID}' (token present: {bool(token)})...")
        
        # Load in bfloat16 for highest fidelity and memory balance
        pipe = FluxPipeline.from_pretrained(
            settings.FLUX_MODEL_ID,
            torch_dtype=torch.bfloat16,
            token=token
        )
        
        # Enable CPU offload to coordinate between 24GB system RAM and GPU
        if settings.FLUX_ENABLE_CPU_OFFLOAD and torch.cuda.is_available():
            logger.info("Enabling CPU offload for FLUX.1...")
            pipe.enable_model_cpu_offload()
        elif torch.cuda.is_available() and settings.FLUX_DEVICE == "cuda":
            pipe.to("cuda")
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            pipe.to("mps")
        else:
            pipe.to("cpu")
            
        _pipeline = pipe
        logger.info("FLUX.1 [schnell] loaded successfully and ready for generation.")
        return _pipeline
        
    except Exception as e:
        logger.error(f"Failed to load FLUX.1 [schnell] pipeline: {e}")
        _pipeline_failed = True
        return None


class FluxGenerator:
    def __init__(self):
        pass

    def generate_panel_art(self, scene_description: str, target_size: Tuple[int, int],
                           genre: str = "Graphic Novel") -> Optional[Image.Image]:
        """
        Generate black-and-white comic panel artwork using FLUX.1 [schnell].
        :param scene_description: Visual description from the story engine
        :param target_size: (width, height) in pixels
        :param genre: Comic genre context
        :return: PIL Image or None if model unavailable
        """
        # 1. External ComfyUI / HTTP endpoint if configured
        if settings.FLUX_API_URL:
            img = self._generate_via_api(scene_description, target_size)
            if img:
                return img

        # 2. Local official FLUX.1 [schnell] pipeline
        pipe = _load_flux_pipeline()
        if pipe is None:
            return None
            
        width, height = target_size
        # Align dimensions to multiples of 16 for FLUX
        gen_w = (width // 16) * 16
        gen_h = (height // 16) * 16
        gen_w = max(256, min(1024, gen_w))
        gen_h = max(256, min(1024, gen_h))
        
        prompt = (
            f"1-bit monochrome e-ink comic panel, {genre} graphic novel style, "
            f"bold crisp black ink outlines, dramatic high contrast chiaroscuro, "
            f"heavy solid black fills, clean white negative space, "
            f"fine crosshatching, vintage manga screentone textures, woodcut line art, "
            f"{scene_description}. "
            f"Sharp ink illustration, pure black and white, no color, no blurry gray gradients, "
            f"crisp distinct silhouettes, optimized for 1-bit e-paper display"
        )
        
        try:
            logger.info(f"Generating panel with FLUX.1 [schnell] ({gen_w}x{gen_h}, {settings.FLUX_INFERENCE_STEPS} steps)...")
            result = pipe(
                prompt=prompt,
                width=gen_w,
                height=gen_h,
                num_inference_steps=settings.FLUX_INFERENCE_STEPS,
                guidance_scale=settings.FLUX_GUIDANCE_SCALE,
                max_sequence_length=256
            )
            raw_img = result.images[0]
            # Convert to grayscale and enhance contrast for clean 1-bit dithering
            from PIL import ImageOps, ImageEnhance
            gray_img = raw_img.convert("L")
            gray_img = ImageOps.autocontrast(gray_img, cutoff=1)
            enhancer = ImageEnhance.Contrast(gray_img)
            gray_img = enhancer.enhance(1.25)
            # Resize exactly to panel target_size
            return gray_img.resize((width, height), Image.Resampling.LANCZOS)
        except Exception as e:
            logger.error(f"Error during FLUX.1 panel generation: {e}")
            return None

    def _generate_via_api(self, prompt: str, target_size: Tuple[int, int]) -> Optional[Image.Image]:
        """Optional HTTP generation if ComfyUI or SD endpoint is running on comack-server."""
        import httpx
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{settings.FLUX_API_URL.rstrip('/')}/v1/images/generations",
                    json={"prompt": prompt, "size": f"{target_size[0]}x{target_size[1]}"}
                )
                if resp.status_code == 200:
                    import base64
                    data = resp.json()
                    b64_str = data["data"][0]["b64_json"]
                    img_bytes = base64.b64decode(b64_str)
                    return Image.open(io.BytesIO(img_bytes)).resize(target_size)
        except Exception as e:
            logger.warning(f"Error querying FLUX API endpoint: {e}")
        return None


flux_generator = FluxGenerator()
