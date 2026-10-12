"""
Spectra 6 Color E-Paper Dithering and Halftoning Engine.
Calibrated for the reTerminal E1002 (Good Display GDEP073E01 / ED2208 7.3" E-Paper).

Lifts key optimization techniques from paperlesspaper/epdoptimize:
1. Measured physical color calibration for the 6-color Spectra 6 pigment gamut.
2. Perceptual color matching using CIELAB Delta-E distance via precomputed 3D LUT.
3. Tone mapping with saturation boost and contrast S-curve for vibrant e-ink rendering.
4. White and Black point preservation: protects dialogue balloons and ink outlines from speckling.
5. Serpentine scanning error diffusion (Floyd-Steinberg kernel) with error decay to prevent worm artifacts.
6. Hardware-accurate 4-bit nibble encoding:
   0x0: Black, 0x1: White, 0x2: Yellow, 0x3: Red, 0x5: Blue, 0x6: Green.
"""
import numpy as np
from PIL import Image, ImageEnhance
from typing import Tuple

# Hardware 4-bit nibbles recognized by GDEP073E01 controller:
# Note: 0x4 was Orange on 7-color ACeP, omitted on 6-color Spectra 6.
PALETTE_NIBBLES = np.array([0x0, 0x1, 0x2, 0x3, 0x5, 0x6], dtype=np.uint8)

# Measured physical RGB values of Spectra 6 pigments under standard light
# (calibrated measurements from epdoptimize 'spectra6' preset)
PALETTE_MEASURED_RGB = np.array([
    [25, 28, 32],      # 0x0 Black  (#191C20)
    [235, 238, 238],   # 0x1 White  (#EBEEEE)
    [210, 205, 10],    # 0x2 Yellow (#D2CD0A)
    [165, 30, 25],     # 0x3 Red    (#A51E19)
    [25, 65, 165],     # 0x5 Blue   (#1941A5)
    [35, 120, 55],     # 0x6 Green  (#237837)
], dtype=np.float32)

# High-fidelity RGB values for the digital preview
PALETTE_PREVIEW_RGB = np.array([
    [0, 0, 0],         # Black
    [255, 255, 255],   # White
    [255, 235, 0],     # Yellow
    [230, 25, 25],     # Red
    [25, 80, 230],     # Blue
    [30, 180, 50],     # Green
], dtype=np.uint8)


def _rgb_to_lab(c: np.ndarray) -> np.ndarray:
    """Convert RGB float array [0..255] to CIELAB."""
    c = np.clip(c / 255.0, 0.0, 1.0)
    mask = c > 0.04045
    lin = np.where(mask, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    X = lin[..., 0] * 0.4124564 + lin[..., 1] * 0.3575761 + lin[..., 2] * 0.1804375
    Y = lin[..., 0] * 0.2126729 + lin[..., 1] * 0.7151522 + lin[..., 2] * 0.0721750
    Z = lin[..., 0] * 0.0193339 + lin[..., 1] * 0.1191920 + lin[..., 2] * 0.9503041
    X /= 0.95047
    Z /= 1.08883
    fx = np.where(X > 0.008856, X ** (1.0 / 3.0), (7.787 * X) + 16.0 / 116.0)
    fy = np.where(Y > 0.008856, Y ** (1.0 / 3.0), (7.787 * Y) + 16.0 / 116.0)
    fz = np.where(Z > 0.008856, Z ** (1.0 / 3.0), (7.787 * Z) + 16.0 / 116.0)
    return np.stack([(116.0 * fy) - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], axis=-1)


def _build_cielab_lut(grid_size: int = 32) -> np.ndarray:
    """Precompute 3D RGB lookup table using CIELAB Delta-E color distance."""
    grid = np.linspace(0, 255, grid_size, dtype=np.float32)
    r, g, b = np.meshgrid(grid, grid, grid, indexing='ij')
    grid_rgb = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    grid_lab = _rgb_to_lab(grid_rgb)
    pal_lab = _rgb_to_lab(PALETTE_MEASURED_RGB)
    # Euclidean distance in CIELAB
    dists = np.linalg.norm(grid_lab[:, None, :] - pal_lab[None, :, :], axis=2)
    return np.argmin(dists, axis=1).reshape(grid_size, grid_size, grid_size).astype(np.uint8)


# Precompute lookup table once at module import (takes ~8ms)
_LUT_32 = _build_cielab_lut(32)


def dither_spectra6(
    image: Image.Image,
    saturation_boost: float = 1.45,
    contrast_boost: float = 1.15,
    white_preservation: bool = True,
    black_preservation: bool = True,
) -> Tuple[Image.Image, bytes]:
    """
    Dither an RGB image to the 6-color Spectra 6 e-paper palette using calibrated
    pigment matching, tone mapping, and serpentine Floyd-Steinberg error diffusion.

    Returns:
        (preview_image: PIL Image in RGB mode, packed_bin: bytes of length W*H/2)
    """
    rgb = image.convert("RGB")
    width, height = rgb.size

    # 1. Tone Mapping (inspired by epdoptimize 'vivid' preset):
    # E-ink reflective pigments are less saturated than emissive displays.
    # Boosting saturation before quantization gives rich primaries and prevents muddy washes.
    if saturation_boost != 1.0:
        rgb = ImageEnhance.Color(rgb).enhance(saturation_boost)
    if contrast_boost != 1.0:
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast_boost)

    arr = np.array(rgb, dtype=np.float32)
    h, w, _ = arr.shape

    out_nibbles = np.zeros((h, w), dtype=np.uint8)
    out_preview = np.zeros((h, w, 3), dtype=np.uint8)

    scale = 31.0 / 255.0

    # 2. Serpentine Error Diffusion
    for y in range(h):
        row = arr[y]
        if y % 2 == 0:
            xs = range(w)
            direction = 1
        else:
            xs = range(w - 1, -1, -1)
            direction = -1

        for x in xs:
            r_val, g_val, b_val = row[x]
            max_c = max(r_val, g_val, b_val)
            min_c = min(r_val, g_val, b_val)

            # White preservation: comic balloons & gutters stay crisp pure white
            if white_preservation and min_c > 220 and (max_c - min_c) < 32:
                idx = 1  # White
            # Black preservation: comic text & ink outlines stay deep solid black
            elif black_preservation and max_c < 42:
                idx = 0  # Black
            else:
                cr = int(max(0.0, min(255.0, r_val)) * scale + 0.5)
                cg = int(max(0.0, min(255.0, g_val)) * scale + 0.5)
                cb = int(max(0.0, min(255.0, b_val)) * scale + 0.5)
                idx = _LUT_32[cr, cg, cb]

            out_nibbles[y, x] = PALETTE_NIBBLES[idx]
            out_preview[y, x] = PALETTE_PREVIEW_RGB[idx]

            # Error decay (85% error retained to avoid smearing across panel borders)
            new_rgb = PALETTE_MEASURED_RGB[idx]
            err_r = (r_val - new_rgb[0]) * 0.85
            err_g = (g_val - new_rgb[1]) * 0.85
            err_b = (b_val - new_rgb[2]) * 0.85

            if direction == 1:
                if x + 1 < w:
                    row[x + 1, 0] += err_r * 0.4375
                    row[x + 1, 1] += err_g * 0.4375
                    row[x + 1, 2] += err_b * 0.4375
                if y + 1 < h:
                    next_row = arr[y + 1]
                    if x > 0:
                        next_row[x - 1, 0] += err_r * 0.1875
                        next_row[x - 1, 1] += err_g * 0.1875
                        next_row[x - 1, 2] += err_b * 0.1875
                    next_row[x, 0] += err_r * 0.3125
                    next_row[x, 1] += err_g * 0.3125
                    next_row[x, 2] += err_b * 0.3125
                    if x + 1 < w:
                        next_row[x + 1, 0] += err_r * 0.0625
                        next_row[x + 1, 1] += err_g * 0.0625
                        next_row[x + 1, 2] += err_b * 0.0625
            else:
                if x > 0:
                    row[x - 1, 0] += err_r * 0.4375
                    row[x - 1, 1] += err_g * 0.4375
                    row[x - 1, 2] += err_b * 0.4375
                if y + 1 < h:
                    next_row = arr[y + 1]
                    if x + 1 < w:
                        next_row[x + 1, 0] += err_r * 0.1875
                        next_row[x + 1, 1] += err_g * 0.1875
                        next_row[x + 1, 2] += err_b * 0.1875
                    next_row[x, 0] += err_r * 0.3125
                    next_row[x, 1] += err_g * 0.3125
                    next_row[x, 2] += err_b * 0.3125
                    if x > 0:
                        next_row[x - 1, 0] += err_r * 0.0625
                        next_row[x - 1, 1] += err_g * 0.0625
                        next_row[x - 1, 2] += err_b * 0.0625

    # Pack 4-bit nibbles: high nibble = pixel 0, low nibble = pixel 1
    p0 = out_nibbles[:, 0::2]
    p1 = out_nibbles[:, 1::2]
    packed = (((p0 & 0x0F) << 4) | (p1 & 0x0F)).tobytes()

    preview_img = Image.fromarray(out_preview, mode="RGB")
    return preview_img, packed
