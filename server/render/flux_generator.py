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
    """Load the image generation pipeline (FLUX or SD-Turbo)."""
    global _pipeline, _pipeline_failed
    if _pipeline is not None or _pipeline_failed:
        return _pipeline
        
    try:
        import torch
        from diffusers import FluxPipeline, AutoPipelineForText2Image
        
        token = os.getenv("HF_TOKEN")
        if not token:
            token_path = Path.home() / ".cache" / "huggingface" / "token"
            if token_path.exists():
                token = token_path.read_text().strip()
                
        model_id = settings.FLUX_MODEL_ID
        is_flux = "flux" in model_id.lower()
        logger.info(f"Loading image model: '{model_id}' (is_flux={is_flux}, token present: {bool(token)})...")
        
        if is_flux:
            pipe = FluxPipeline.from_pretrained(
                model_id,
                torch_dtype=torch.bfloat16,
                token=token
            )
            if settings.FLUX_ENABLE_CPU_OFFLOAD and torch.cuda.is_available():
                logger.info("Enabling CPU offload for FLUX...")
                pipe.enable_model_cpu_offload()
            elif torch.cuda.is_available() and settings.FLUX_DEVICE == "cuda":
                pipe.to("cuda")
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                pipe.to("mps")
            else:
                pipe.to("cpu")
        else:
            use_cuda = torch.cuda.is_available() and settings.FLUX_DEVICE == "cuda"
            # fp16 only on the GPU; fp16 on CPU is ~10x slower than fp32.
            pipe = AutoPipelineForText2Image.from_pretrained(
                model_id,
                torch_dtype=torch.float16 if use_cuda else torch.float32,
                variant="fp16" if use_cuda else None,
                token=token
            )
            if use_cuda:
                pipe.to("cuda")
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                pipe.to("mps")
            else:
                pipe.to("cpu")
            
        _pipeline = pipe
        logger.info(f"Model '{model_id}' loaded successfully and ready for generation.")
        return _pipeline
        
    except Exception as e:
        logger.error(f"Failed to load image generation pipeline: {e}")
        _pipeline_failed = True
        return None


class FluxGenerator:
    def __init__(self):
        pass

    def generate_panel_art(self, scene_description: str, target_size: Tuple[int, int],
                           genre: str = "Graphic Novel") -> Optional[Image.Image]:
        """
        Generate black-and-white comic panel artwork.
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

        # 2. Local diffusion pipeline
        pipe = _load_flux_pipeline()
        if pipe is None:
            return None
            
        width, height = target_size
        model_id = settings.FLUX_MODEL_ID.lower()
        is_flux = "flux" in model_id
        is_turbo = "turbo" in model_id
        
        # Dimensions must align to model requirements
        multiple = 16 if is_flux else 8
        gen_w = (width // multiple) * multiple
        gen_h = (height // multiple) * multiple
        
        if is_turbo:
            gen_w = min(512, max(256, gen_w))
            gen_h = min(512, max(256, gen_h))
            steps = 1
            guidance = 0.0
        else:
            gen_w = max(256, min(1024, gen_w))
            gen_h = max(256, min(1024, gen_h))
            steps = settings.FLUX_INFERENCE_STEPS
            guidance = settings.FLUX_GUIDANCE_SCALE
        
        # SD-Turbo's CLIP encoder reads only 77 tokens: short style prefix first, then the scene.
        prompt = (
            f"black and white ink comic panel, bold outlines, high contrast, solid blacks, "
            f"crosshatching, no gray: {scene_description}"
        )
        
        try:
            logger.info(f"Generating panel with {settings.FLUX_MODEL_ID} ({gen_w}x{gen_h}, {steps} steps)...")
            kwargs = {
                "prompt": prompt,
                "width": gen_w,
                "height": gen_h,
                "num_inference_steps": steps,
                "guidance_scale": guidance,
            }
            if is_flux:
                kwargs["max_sequence_length"] = 256
                
            result = pipe(**kwargs)
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
            logger.error(f"Error during panel generation: {e}")
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
