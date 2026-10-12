"""Panel image prompts: text_anchor normalization and clear-space wording for FLUX."""

# Balloons/captions are composited in code; the model must not paint them into the art.
_NO_TEXT = (
    "Strictly visual only: no speech bubbles, no dialogue balloons, "
    "no captions, no text, no lettering, no sound-effect lettering."
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


def panel_art_prompt(scene_description: str, art_style: str = "",
                     text_anchor: str = "top") -> str:
    """Image-model prompt for one panel; speech balloons are drawn later by the compositor.

    `art_style` is the arc bible value — always appended so the series look is used
    even when the script model forgot to put it in scene_description.
    `text_anchor` steers clear negative space away from faces for lettering.
    """
    style = f" Art style: {art_style}." if (art_style or "").strip() else ""
    clear = _TEXT_ANCHOR_CLEAR[normalize_text_anchor(text_anchor)]
    return f"comic panel: {scene_description}.{style} {clear} {_NO_TEXT}"
