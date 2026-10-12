"""Speech balloons, narration boxes, and SFX placement inside panel text boxes."""
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import ImageDraw, ImageFont


def get_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Load default font or system TTF if available."""
    try:
        font_names = [
            "/System/Library/Fonts/Supplemental/Comic Sans MS.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ]
        for fn in font_names:
            if Path(fn).exists():
                return ImageFont.truetype(fn, size)
    except Exception:
        pass
    return ImageFont.load_default()


def wrap_text(text: str, font: ImageFont.ImageFont, max_width: int, draw: ImageDraw.ImageDraw) -> List[str]:
    """Wrap text to fit within max_width pixels."""
    words = text.split()
    if not words:
        return []
    lines = []
    current_line = []

    for word in words:
        test_line = " ".join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        w = bbox[2] - bbox[0]
        if w <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]

    if current_line:
        lines.append(" ".join(current_line))
    return lines


def narration_height(draw: ImageDraw.ImageDraw, max_width: int, text: str) -> int:
    font = get_font(11, bold=True)
    lines = wrap_text(text, font, max_width - 12, draw)
    return (len(lines) * 14) + 10 if lines else 0


def speech_balloon_height(draw: ImageDraw.ImageDraw, max_width: int, text: str) -> int:
    font = get_font(11, bold=False)
    lines = wrap_text(text, font, max_width - 16, draw)
    return (len(lines) * 14) + 12 + 10 if lines else 0  # + tail


def draw_narration_box(draw: ImageDraw.ImageDraw, x: int, y: int, max_width: int, text: str) -> int:
    """Draw a rectangular narrator caption box with drop shadow."""
    font = get_font(11, bold=True)
    lines = wrap_text(text, font, max_width - 12, draw)
    if not lines:
        return y

    line_height = 14
    box_h = (len(lines) * line_height) + 10
    box_w = max_width

    draw.rectangle([x + 3, y + 3, x + box_w + 3, y + box_h + 3], fill=(0, 0, 0))
    draw.rectangle([x, y, x + box_w, y + box_h], fill=(255, 255, 255), outline=(0, 0, 0), width=2)

    for i, line in enumerate(lines):
        draw.text((x + 6, y + 5 + i * line_height), line, fill=(0, 0, 0), font=font)

    return y + box_h + 10


def draw_speech_balloon(draw: ImageDraw.ImageDraw, x: int, y: int,
                        max_width: int, text: str, points_left: bool = True) -> None:
    """Draw a classic rounded comic speech balloon with directional tail."""
    font = get_font(11, bold=False)
    lines = wrap_text(text, font, max_width - 16, draw)
    if not lines:
        return

    line_height = 14
    box_h = (len(lines) * line_height) + 12
    max_line_w = max(draw.textbbox((0, 0), line, font=font)[2] for line in lines)
    box_w = min(max_width, max(max_line_w + 16, 70))

    draw.rounded_rectangle([x, y, x + box_w, y + box_h], radius=7, fill=(255, 255, 255), outline=(0, 0, 0), width=2)

    if points_left:
        tail = [(x + 12, y + box_h - 1), (x + 6, y + box_h + 10), (x + 22, y + box_h - 1)]
    else:
        tail = [(x + box_w - 22, y + box_h - 1), (x + box_w - 6, y + box_h + 10), (x + box_w - 12, y + box_h - 1)]

    draw.polygon(tail, fill=(255, 255, 255))
    draw.line([tail[0], tail[1], tail[2]], fill=(0, 0, 0), width=2)
    draw.line([(tail[0][0] + 1, y + box_h), (tail[2][0] - 1, y + box_h)], fill=(255, 255, 255), width=2)

    for i, line in enumerate(lines):
        draw.text((x + 8, y + 6 + i * line_height), line, fill=(0, 0, 0), font=font)


def draw_lettering(draw: ImageDraw.ImageDraw, text_box: Tuple[int, int, int, int],
                   panel_data: Dict[str, Any], anchor: str) -> None:
    """Park captions and balloons in the text_anchor zone so they sit on clear art space."""
    x0, y0, x1, y1 = text_box
    pw, ph = x1 - x0, y1 - y0
    pad = 8
    max_w = min(pw - 2 * pad, 220)
    stack_down = not anchor.startswith("bottom")

    if anchor.endswith("right"):
        base_x = x1 - pad - max_w
    elif anchor in ("top", "bottom"):
        base_x = x0 + (pw - max_w) // 2
    else:
        base_x = x0 + pad

    if stack_down:
        cursor_y = y0 + pad
    else:
        cursor_y = y1 - pad

    narration = panel_data.get("narration")
    if narration:
        if stack_down:
            cursor_y = draw_narration_box(draw, base_x, cursor_y, max_w, narration)
        else:
            h = narration_height(draw, max_w, narration)
            cursor_y -= h
            draw_narration_box(draw, base_x, cursor_y, max_w, narration)
            cursor_y -= 10

    balloons = panel_data.get("speech_balloons") or []
    for b_idx, balloon in enumerate(balloons):
        b_text = balloon.get("text", "")
        b_char = balloon.get("character", "")
        if not b_text:
            continue
        full_text = f"{b_char.upper()}: {b_text}" if b_char else b_text
        stagger = (b_idx % 3) * 12
        if anchor.endswith("right"):
            bx = max(x0 + pad, base_x - stagger)
        else:
            bx = min(x1 - pad - max_w, base_x + stagger)
        points_left = not anchor.endswith("right")
        if stack_down:
            bh = speech_balloon_height(draw, max_w, full_text)
            if cursor_y + bh > y1 - pad:
                break
            draw_speech_balloon(draw, bx, cursor_y, max_w, full_text, points_left=points_left)
            cursor_y += bh + 8
        else:
            bh = speech_balloon_height(draw, max_w, full_text)
            cursor_y -= bh
            if cursor_y < y0 + pad:
                break
            draw_speech_balloon(draw, bx, cursor_y, max_w, full_text, points_left=points_left)
            cursor_y -= 8

    sfx = panel_data.get("sound_effect")
    if sfx:
        sfx_font = get_font(20, bold=True)
        if stack_down:
            sfx_y = y1 - 45
        else:
            sfx_y = y0 + 12
        if anchor.endswith("right"):
            sfx_x = x0 + 12
        elif anchor.endswith("left"):
            sfx_x = x1 - 80
        else:
            sfx_x = x0 + (pw // 2) - 30
        draw.text((sfx_x + 2, sfx_y + 2), sfx.upper(), fill=(0, 0, 0), font=sfx_font)
        draw.text((sfx_x, sfx_y), sfx.upper(), fill=(0, 0, 0), font=sfx_font)
