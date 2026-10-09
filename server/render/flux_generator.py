"""
FLUX.1 [schnell] Comic Art Generator.
Exclusively uses black-forest-labs/FLUX.1-schnell for state-of-the-art
high-contrast graphic novel panel illustrations.
"""
import io
import time
import logging
from typing import Optional, Tuple
from pathlib import Path
from PIL import Image

from server import llm
from server.config import settings

logger = logging.getLogger("comack.flux")

_pipeline = None

# Seconds to wait between attempts at a panel; after the last one the comic is abandoned.
RETRY_DELAYS_S = (15, 30, 60, 120, 240)


class FluxUnavailable(Exception):
    """No local diffusion stack at all (e.g. a dev machine): retrying can't help."""


class PanelArtFailed(RuntimeError):
    """Every attempt at a panel failed; the comic should not be published."""


def _load_flux_pipeline():
    """Load the image generation pipeline (FLUX or SD-Turbo). Raises on failure."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    try:
        import torch
        from diffusers import FluxPipeline, AutoPipelineForText2Image
    except ImportError as e:
        raise FluxUnavailable(e)

    token = settings.HF_TOKEN
    if not token:
        token_path = Path.home() / ".cache" / "huggingface" / "token"
        if token_path.exists():
            token = token_path.read_text().strip()

    model_id = settings.FLUX_MODEL_ID
    is_flux = "flux" in model_id.lower()
    logger.info(f"Loading image model: '{model_id}' (is_flux={is_flux}, token present: {bool(token)})...")

    if is_flux:
        from diffusers import FluxTransformer2DModel, GGUFQuantizationConfig
        # 4-bit GGUF transformer (6.8 GB) instead of the 24 GB bf16 one; the text
        # encoders and VAE come from the official repo. Model CPU offload keeps only one
        # component on the GPU at a time (peak ~10 GB: the T5 encoder).
        transformer = FluxTransformer2DModel.from_single_file(
            settings.FLUX_GGUF_URL,
            quantization_config=GGUFQuantizationConfig(compute_dtype=torch.bfloat16),
            torch_dtype=torch.bfloat16,
            config=model_id, subfolder="transformer", token=token
        )
        pipe = FluxPipeline.from_pretrained(
            model_id, transformer=transformer, torch_dtype=torch.bfloat16, token=token
        )
        pipe.enable_model_cpu_offload()
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


def _wait_for_vram(min_free_gb: float, timeout_s: float) -> bool:
    """The 26B LLM holds ~11 GB of the 12 GB GPU while awake and sleeps 60s after its last
    request. Wait for it instead of fighting it for memory."""
    import torch
    deadline = time.time() + timeout_s
    while True:
        # Our own cached blocks are reusable, so they count as free.
        free_gb = (torch.cuda.mem_get_info()[0] + torch.cuda.memory_reserved()) / 1e9
        if free_gb >= min_free_gb:
            return True
        if time.time() >= deadline:
            raise RuntimeError(f"only {free_gb:.1f} GB VRAM free after {timeout_s:.0f}s")
        time.sleep(5)


def release_pipeline():
    """Free the pipeline after a comic (~17 GB of RAM while loaded; comics are minutes apart)."""
    global _pipeline
    _pipeline = None
    try:
        import torch
    except ImportError:
        return
    import gc
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()  # also drops an OOM's half-built tensors
    logger.info("Image pipeline released.")


class FluxGenerator:
    def __init__(self):
        pass

    def generate_panel_art(self, scene_description: str, target_size: Tuple[int, int],
                           genre: str = "Graphic Novel") -> Optional[Image.Image]:
        """
        Generate comic panel artwork.
        :param scene_description: Visual description from the story engine
        :param target_size: (width, height) of the panel in pixels; the image comes back at the
            model's own resolution with about this aspect ratio (the compositor saves and fits it)
        :param genre: Comic genre context
        :return: the model's original RGB image, or None only when no local pipeline exists at all
        :raises PanelArtFailed: every retry failed (remote-only mode: the image API never answered)
        """
        # 1. Remote-only mode: FLUX_API_URL set means the OpenAI-compatible image API is
        #    the only backend — the local diffusers stack (torch import, GGUF download,
        #    VRAM guard) is never touched, even when every attempt fails: exhaustion
        #    raises PanelArtFailed, which the compositor already handles.
        if settings.FLUX_API_URL:
            prompt = f"comic panel: {scene_description}"
            for attempt, delay in enumerate((*RETRY_DELAYS_S, None), 1):
                try:
                    return self._generate_via_api(prompt, target_size)
                except Exception as e:
                    if delay is None:
                        raise PanelArtFailed(
                            f"remote generation failed after {attempt} attempts: {e}") from e
                    logger.warning(f"Remote panel attempt {attempt} failed ({e}); retrying in {delay}s.")
                    time.sleep(delay)

        # 2. Local diffusion pipeline, retried with backoff (OOM, LLM hogging VRAM, load errors)
        return self._generate_local_loop(scene_description, target_size)

    def _generate_local_loop(self, scene_description: str, target_size: Tuple[int, int]) -> Optional[Image.Image]:
        """The local path, unchanged: retried with backoff; None (procedural art) when
        no local pipeline exists at all."""
        for attempt, delay in enumerate((*RETRY_DELAYS_S, None), 1):
            try:
                return self._generate_local(scene_description, target_size)
            except FluxUnavailable as e:
                logger.warning(f"No local image pipeline ({e}); using procedural art.")
                return None
            except Exception as e:
                # An OOM can leave offloaded modules split across devices; start clean.
                release_pipeline()
                if delay is None:
                    raise PanelArtFailed(f"panel failed after {attempt} attempts: {e}") from e
                logger.warning(f"Panel attempt {attempt} failed ({e}); retrying in {delay}s.")
                time.sleep(delay)

    def _generate_local(self, scene_description: str, target_size: Tuple[int, int]) -> Image.Image:
        width, height = target_size
        model_id = settings.FLUX_MODEL_ID.lower()
        is_flux = "flux" in model_id
        is_turbo = "turbo" in model_id

        pipe = _load_flux_pipeline()
        if is_flux:
            _wait_for_vram(settings.FLUX_MIN_FREE_VRAM_GB, settings.FLUX_VRAM_WAIT_SECONDS)

        # Dimensions must align to model requirements
        multiple = 16 if is_flux else 8
        gen_w = (width // multiple) * multiple
        gen_h = (height // multiple) * multiple

        if is_flux:
            # schnell: 4 steps, no guidance; render up to 2x (max 1024 px side) and downscale.
            scale = min(2.0, 1024 / max(width, height))
            gen_w = int(width * scale) // 16 * 16
            gen_h = int(height * scale) // 16 * 16
            steps = 4
            guidance = 0.0
        elif is_turbo:
            gen_w = min(512, max(256, gen_w))
            gen_h = min(512, max(256, gen_h))
            steps = 1
            guidance = 0.0
        else:
            gen_w = max(256, min(1024, gen_w))
            gen_h = max(256, min(1024, gen_h))
            steps = settings.FLUX_INFERENCE_STEPS
            guidance = settings.FLUX_GUIDANCE_SCALE
        
        # The art style comes from the arc, via the scene description; halftoning happens after.
        prompt = f"comic panel: {scene_description}"
        
        logger.info(f"Generating panel with {settings.FLUX_MODEL_ID} ({gen_w}x{gen_h}, {steps} steps)...")
        kwargs = {
            "prompt": prompt,
            "width": gen_w,
            "height": gen_h,
            "num_inference_steps": steps,
            "guidance_scale": guidance,
        }
        if is_flux:
            kwargs["max_sequence_length"] = 512

        entry = llm.log_call(settings.FLUX_MODEL_ID, "", prompt)
        raw_img = pipe(**kwargs).images[0]
        entry["output"] = f"[{gen_w}x{gen_h} image, {steps} steps]"
        return raw_img.convert("RGB")

    def _generate_via_api(self, prompt: str, target_size: Tuple[int, int]) -> Image.Image:
        """One OpenAI-compatible image request: POST {base}/v1/images/generations
        (llm.api_url joins, so the base may or may not already end in /v1). Asks for
        base64 explicitly; a provider that ignored that and answered with a URL gets
        its bytes fetched. Raises on any failure — the retry loop in
        generate_panel_art owns the backoff, so nothing here degrades into None."""
        import base64

        import httpx

        headers = {}
        if settings.FLUX_API_KEY:
            headers["Authorization"] = f"Bearer {settings.FLUX_API_KEY}"
        payload: dict = {
            "prompt": prompt,
            "n": 1,
            "size": f"{target_size[0]}x{target_size[1]}",
            "response_format": "b64_json",
        }
        if settings.FLUX_API_MODEL:
            payload["model"] = settings.FLUX_API_MODEL

        # Remote calls show up in /api/prompts like local renders do.
        entry = llm.log_call(settings.FLUX_API_MODEL or settings.FLUX_MODEL_ID, "", prompt)
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(llm.api_url(settings.FLUX_API_URL, "images/generations"),
                                   json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()["data"][0]
                if data.get("b64_json"):
                    img = Image.open(io.BytesIO(base64.b64decode(data["b64_json"])))
                elif data.get("url"):
                    with client.stream("GET", data["url"]) as dl:
                        dl.raise_for_status()
                        img = Image.open(io.BytesIO(dl.read()))
                else:
                    raise ValueError(f"no b64_json or url in response: {list(data)}")
            entry["output"] = f"[remote image {img.width}x{img.height}]"
            return img.convert("RGB")
        except Exception as e:
            logger.warning(f"Remote image API error ({settings.FLUX_API_URL}): {e}")
            raise


flux_generator = FluxGenerator()
