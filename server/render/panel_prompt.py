"""Panel image prompts: text_anchor normalization and clear-space wording for FLUX."""

import re

# Balloons/captions are composited in code; the model must not paint them into the art.
_NO_TEXT = (
    "Strictly visual only: no speech bubbles, no dialogue balloons, "
    "no captions, no text, no lettering, no sound-effect lettering."
)

# Page layouts may be diagonal polygons; each panel's *artwork* must stay level —
# a tilted border inside the bitmap looks broken when pasted into the page.
_LEVEL_FRAME = (
    "Level, axis-aligned frame: keep the horizon upright; no dutch angle, "
    "no tilted or rotated panel border, no slanted camera, no crooked frame."
)

# Script models often write "Tilted panel…" when they mean kinetic energy.
_TILT_PHRASES = (
    re.compile(r"(?i)\btilted\s+panel\b\s*,?"),
    re.compile(r"(?i)\bthe\s+frame\s+angled(?:\s+\w+)?\b\s*,?"),
    re.compile(r"(?i)\bdutch\s+angle\b\s*,?"),
    re.compile(r"(?i)\bcanted\s+(?:camera|angle|frame)\b\s*,?"),
    re.compile(r"(?i)\bslanted\s+(?:camera|frame|panel|shot)\b\s*,?"),
    # Keep a readable opener; only the "slanted" instruction is wrong.
    # Handled specially in strip_tilt_direction.
    re.compile(r"(?i)\bangled\s+(?:sharply\s+)?(?:frame|panel|shot)\b\s*,?"),
    re.compile(r"(?i)\bcamera\s+tilted\b\s*,?"),
    re.compile(r"(?i)\brotated\s+(?:frame|panel|border)\b\s*,?"),
    re.compile(r"(?i)\bcrooked\s+frame\b\s*,?"),
    re.compile(r"(?i)\ba\s+sweeping\s+upward\s+slant\b\s*,?"),
)


# Where lettering will be composited — art must leave empty space there.
_TEXT_ANCHOR_CLEAR = {
    "top": "Leave clear empty negative space across the top third for lettering; "
           "put faces and key subjects in the middle and lower areas.",
    "top-left": "Leave clear empty negative space in the upper-left for lettering; "
                "put faces and key subjects toward the right and lower areas.",
    "top-right": "Leave clear empty negative space in the upper-right for lettering; "
                 "put faces and key subjects toward the left and lower areas.",
    "bottom": "Leave clear empty negative space across the bottom third for lettering; "
              "put faces and key subjects in the upper areas.",
    "bottom-left": "Leave clear empty negative space in the lower-left for lettering; "
                   "put faces and key subjects toward the right and upper areas.",
    "bottom-right": "Leave clear empty negative space in the lower-right for lettering; "
                    "put faces and key subjects toward the left and upper areas.",
}


def normalize_text_anchor(anchor: str | None) -> str:
    """One of the supported lettering zones; default top (classic comic caption band)."""
    key = (anchor or "top").strip().lower().replace("_", "-")
    return key if key in _TEXT_ANCHOR_CLEAR else "top"


def strip_tilt_direction(scene_description: str) -> str:
    """Drop dutch-angle / tilted-frame instructions the script model sneaks in.

    Kinetic page layouts are drawn by the compositor as polygon gutters; tilting
    the bitmap itself leaves crooked borders inside axis-aligned panel boxes.
    """
    text = (scene_description or "").strip()
    if not text:
        return text
    text = re.sub(r"(?i)\bwide\s+slanted\s+opener\b", "Wide opener", text)
    text = re.sub(r"(?i)\bslanted\s+opener\b", "Wide opener", text)
    for pat in _TILT_PHRASES:
        text = pat.sub(" ", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s*,\s*,+", ", ", text)
    text = re.sub(r"^\s*[.,;:\-]+\s*", "", text)
    return text.strip()


def panel_art_prompt(scene_description: str, art_style: str = "",
                     text_anchor: str = "top") -> str:
    """Image-model prompt for one panel; speech balloons are drawn later by the compositor.

    `art_style` is the arc bible value — always appended so the series look is used
    even when the script model forgot to put it in scene_description.
    `text_anchor` steers clear negative space away from faces for lettering.
    """
    scene = strip_tilt_direction(scene_description)
    style = f" Art style: {art_style}." if (art_style or "").strip() else ""
    clear = _TEXT_ANCHOR_CLEAR[normalize_text_anchor(text_anchor)]
    return f"comic panel: {scene}.{style} {clear} {_LEVEL_FRAME} {_NO_TEXT}"
