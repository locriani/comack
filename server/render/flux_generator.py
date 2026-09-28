"""
FLUX.1 [schnell] Comic Art Generator.
Generates high-contrast, masterwork black-and-white ink illustrations for comic panels
using local diffusers with FP8 / bfloat16 and CPU offload, or via ComfyUI endpoint.
"""
import io
import logging
from typing import Optional, Tuple
from pathlib import Path
from PIL import Image

from server.config import settings

logger = logging.getLogger("comack.flux")

_flux_pipeline = None
_flux_failed = False


def _get_flux_pipeline():
    """Lazy-load the FLUX.1 pipeline on first generation request."""
    global _flux_pipeline, _flux_failed
    if _flux_pipeline is not None or _flux_failed:
        return _flux_pipeline
        
    try:
        import torch
        from diffusers import FluxPipeline
        
        logger.info(f"Loading FLUX.1 model: '{settings.FLUX_MODEL_ID}' on {settings.FLUX_DEVICE}...")
        
        torch_dtype = torch.bfloat16
        pipe = FluxPipeline.from_pretrained(
            settings.FLUX_MODEL_ID,
            torch_dtype=torch_dtype
        )
        
        if settings.FLUX_ENABLE_CPU_OFFLOAD and torch.cuda.is_available():
            logger.info("Enabling CPU offload for FLUX.1 to optimize VRAM...")
            pipe.enable_model_cpu_offload()
        elif torch.cuda.is_available() and settings.FLUX_DEVICE == "cuda":
            pipe.to("cuda")
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            pipe.to("mps")
        else:
            pipe.to("cpu")
            
        _flux_pipeline = pipe
        logger.info("FLUX.1 pipeline loaded successfully.")
    except Exception as e:
        logger.warning(
            f"Could not load local FLUX pipeline ({e}). "
            f"Ensure torch and diffusers are installed on comack-server. Using line-art fallback."
        )
        _flux_failed = True
        _flux_pipeline = None
        
    return _flux_pipeline


class FluxGenerator:
    def __init__(self):
        pass

    def generate_panel_art(self, scene_description: str, target_size: Tuple[int, int],
                           genre: str = "Graphic Novel") -> Optional[Image.Image]:
        """
        Generate black-and-white comic panel artwork for a given scene description.
        :param scene_description: Visual description from the story engine
        :param target_size: (width, height) in pixels
        :param genre: Comic genre context
        :return: PIL Image or None if model unavailable
        """
        # 1. Check if external ComfyUI / HTTP endpoint is specified
        if settings.FLUX_API_URL:
            img = self._generate_via_api(scene_description, target_size)
            if img:
                return img

        # 2. Local diffusers pipeline
        pipe = _get_flux_pipeline()
        if pipe is None:
            return None
            
        width, height = target_size
        # Align dimensions to multiples of 16 for diffusion models
        gen_w = (width // 16) * 16
        gen_h = (height // 16) * 16
        gen_w = max(256, min(1024, gen_w))
        gen_h = max(256, min(1024, gen_h))
        
        prompt = (
            f"Masterpiece graphic novel comic panel, {genre} style, "
            f"bold clean black ink lines, dramatic chiaroscuro lighting, deep shadows, "
            f"detailed manga crosshatching, halftone dots, high contrast ink illustration. "
            f"{scene_description}. "
            f"Sharp ink linework, stark monochrome, vintage graphic print, comic art, 8k resolution, no color"
        )
        
        try:
            logger.info(f"Generating panel with FLUX.1 ({gen_w}x{gen_h}, {settings.FLUX_INFERENCE_STEPS} steps)...")
            result = pipe(
                prompt=prompt,
                width=gen_w,
                height=gen_h,
                num_inference_steps=settings.FLUX_INFERENCE_STEPS,
                guidance_scale=settings.FLUX_GUIDANCE_SCALE,
                max_sequence_length=256
            )
            raw_img = result.images[0]
            # Resize exactly to target_size
            return raw_img.resize((width, height), Image.Resampling.LANCZOS)
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
