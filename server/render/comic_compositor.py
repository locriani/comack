"""
800x480 Comic Page Compositor and E-Paper Dithering Engine.
Assembles comic pages from common layouts (LAYOUTS), renders speech balloons, narrator boxes,
and sound effects, then applies Floyd-Steinberg error-diffusion dithering.
Outputs both PNG preview and 48,000-byte 1-bit packed binary for the reTerminal E1001.
"""
import math
import numpy as np
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from server.config import settings, COMICS_DIR

WIDTH = settings.DISPLAY_WIDTH   # 800
HEIGHT = settings.DISPLAY_HEIGHT # 480

T = 1 / 3


def R(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# Common comic page layouts: name -> (what scene it suits, panels in reading order). Each panel is a
# quad (top-left, top-right, bottom-right, bottom-left) in fractions of the page area; later panels
# draw over earlier ones (the inset). The first layout for each panel count is the plain fallback.
LAYOUTS = {
    "splash":         ("one full-page splash: a single dramatic climax, reveal, or quiet contemplative moment",
                       [R(0, 0, 1, 1)]),
    "two-columns":    ("two side-by-side panels: back-and-forth dialogue, before/after",
                       [R(0, 0, .5, 1), R(.5, 0, 1, 1)]),
    "splash-inset":   ("full-page splash with a small close-up inset: a big moment plus someone's reaction to it",
                       [R(0, 0, 1, 1), R(.64, .06, .97, .46)]),
    "two-tiers":      ("two wide stacked panels: cause, then effect",
                       [R(0, 0, 1, .5), R(0, .5, 1, 1)]),
    "diagonal-split": ("two panels cut by a slash: a clash, confrontation, or sudden reversal",
                       [[(0, 0), (.55, 0), (.45, 1), (0, 1)], [(.55, 0), (1, 0), (1, 1), (.45, 1)]]),
    "slanted-tiers":  ("two tiers cut by a slope: a fall, an impact, things going sideways",
                       [[(0, 0), (1, 0), (1, .42), (0, .58)], [(0, .58), (1, .42), (1, 1), (0, 1)]]),
    "three-columns":  ("classic strip: setup, escalation, punchline",
                       [R(0, 0, T, 1), R(T, 0, 2 * T, 1), R(2 * T, 0, 1, 1)]),
    "widescreen":     ("three cinematic stacked strips: a slow, sweeping sequence or passage of time",
                       [R(0, 0, 1, T), R(0, T, 1, 2 * T), R(0, 2 * T, 1, 1)]),
    "big-left":       ("a big opening shot, then two small beats stacked on the right",
                       [R(0, 0, .6, 1), R(.6, 0, 1, .5), R(.6, .5, 1, 1)]),
    "big-right":      ("two small beats stacked on the left, then a big payoff",
                       [R(0, 0, .4, .5), R(0, .5, .4, 1), R(.4, 0, 1, 1)]),
    "wide-top":       ("a wide establishing shot, then two beats below",
                       [R(0, 0, 1, .5), R(0, .5, .5, 1), R(.5, .5, 1, 1)]),
    "wide-bottom":    ("two quick beats, then a wide punchline",
                       [R(0, 0, .5, .5), R(.5, 0, 1, .5), R(0, .5, 1, 1)]),
    "diagonal-three": ("three slanted columns: fast action, a chase, momentum",
                       [[(0, 0), (.36, 0), (.30, 1), (0, 1)], [(.36, 0), (.70, 0), (.64, 1), (.30, 1)],
                        [(.70, 0), (1, 0), (1, 1), (.64, 1)]]),
    "grid":           ("2x2 grid: four even beats, a conversation or a countdown",
                       [R(0, 0, .5, .5), R(.5, 0, 1, .5), R(0, .5, .5, 1), R(.5, .5, 1, 1)]),
    "wide-top-three": ("a wide establishing shot, then three quick beats",
                       [R(0, 0, 1, .45), R(0, .45, T, 1), R(T, .45, 2 * T, 1), R(2 * T, .45, 1, 1)]),
    "action":         ("a wide slanted opener, then three tilted beats: chaos breaking loose",
                       [[(0, 0), (1, 0), (1, .38), (0, .52)], [(0, .52), (.36, .47), (.30, 1), (0, 1)],
                        [(.36, .47), (.70, .42), (.64, 1), (.30, 1)], [(.70, .42), (1, .38), (1, 1), (.64, 1)]]),
    "five":           ("two panels over three: a busy, escalating scene",
                       [R(0, 0, .5, .5), R(.5, 0, 1, .5), R(0, .5, T, 1), R(T, .5, 2 * T, 1), R(2 * T, .5, 1, 1)]),
}
MAX_PANELS = max(len(p) for _, p in LAYOUTS.values())


def resolve_layout(name: str, panel_count: int) -> str:
    """The requested layout if it fits the panel count, else the plain layout for that count."""
    if name in LAYOUTS and len(LAYOUTS[name][1]) == panel_count:
        return name
    n = min(max(panel_count, 1), MAX_PANELS)
    return next(k for k, (_, p) in LAYOUTS.items() if len(p) == n)


def _get_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Load default font or system TTF if available."""
    try:
        # Try finding standard system fonts on mac or linux
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


def dither_to_1bit_monochrome(image: Image.Image) -> Tuple[Image.Image, bytes]:
    """
    Apply high-contrast Floyd-Steinberg error diffusion dithering.
    Returns: (monochrome_pil_image, packed_48000_bytes)
    """
    from PIL import ImageOps, ImageEnhance
    gray = image.convert("L")
    # Enhance contrast and stretch dynamic range for punchy e-ink blacks and whites
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = ImageEnhance.Contrast(gray).enhance(1.2)
    dithered = gray.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    
    # Mode "1" tobytes() is already the EPD format: 1 bit/pixel, MSB first, 100 bytes/row, 1 = White
    return dithered, dithered.tobytes()


class ComicCompositor:
    def __init__(self):
        self.banner_height = 28
        self.margin = 8
        self.gutter = 8

    def compose_comic(self, script: Dict[str, Any]) -> Tuple[Path, Path]:
        """
        Compose an 800x480 comic strip based on script JSON.
        Returns: (png_path, bin_path)
        """
        issue_number = script.get("issue_number", 1)
        panels = script.get("panels", [])
        layout_type = resolve_layout(script.get("layout_type", ""), len(panels))

        # Base canvas in RGB
        canvas = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))

        # 1. Render Top Header Banner
        self._draw_header(canvas, script)

        # 2. Panel area, grown by half a gutter since each panel shrinks by that much
        h = self.gutter // 2
        left, top = self.margin - h, self.banner_height + self.margin - h
        area_w, area_h = WIDTH - self.margin + h - left, HEIGHT - self.margin + h - top

        # 3. Each panel is a polygon mask: white under it (an inset's gutter), art inside it shrunk
        #    by half a gutter, then a black ring for the frame
        from server.render.flux_generator import release_pipeline
        try:
            originals = self._draw_panels(canvas, layout_type, panels, left, top, area_w, area_h, h)
        finally:  # also when a panel gives up (PanelArtFailed): the pipeline holds ~17 GB of RAM
            release_pipeline()

        # 4. Keep the originals: each panel's art as the model made it, and the full-color page
        #    before halftoning (so a better halftoner or display can redo the page later)
        stem = COMICS_DIR / f"issue_{issue_number:04d}"
        for i, art in enumerate(originals, 1):
            if art is not None:
                art.save(f"{stem}_panel{i}.png")
        canvas.save(f"{stem}_color.png")

        # 5. Dither and Export
        dithered_img, bin_bytes = dither_to_1bit_monochrome(canvas)

        # Ensure 48,000 bytes exactly
        assert len(bin_bytes) == (WIDTH * HEIGHT // 8), f"Bin size must be 48000, got {len(bin_bytes)}"

        # Only the numbered issue: /api/comics/current.* serves whichever issue is displayed
        png_path = COMICS_DIR / f"issue_{issue_number:04d}.png"
        bin_path = COMICS_DIR / f"issue_{issue_number:04d}.bin"
        dithered_img.save(png_path)
        bin_path.write_bytes(bin_bytes)
        return png_path, bin_path

    def _draw_header(self, img: Image.Image, script: Dict[str, Any]):
        """The black banner: series title, issue number within the series, issue title."""
        issue_number = script.get("issue_number", 1)
        series_issue = script.get("series_issue", issue_number)  # printed; issue_number names the files
        title = script.get("title", f"Issue #{series_issue}")
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, WIDTH, self.banner_height], fill="black")
        arc_title = script.get("arc_title") or "COMACK"
        header_text = f"{arc_title.upper()}  •  ISSUE #{series_issue:03d}  •  {title.upper()}"
        size = 14
        header_font = _get_font(size, bold=True)
        while size > 8 and draw.textlength(header_text, font=header_font) > WIDTH - 2 * self.margin:
            size -= 1  # shrink long titles to fit the banner
            header_font = _get_font(size, bold=True)
        draw.text((self.margin, 6), header_text, fill="white", font=header_font)

    def redraw_header(self, script: Dict[str, Any]):
        """Repaint an issue's banner on its saved pages (no new art). The banner is pure black and
        white, so painting it straight onto the 1-bit page is exact."""
        stem = COMICS_DIR / f"issue_{script['issue_number']:04d}"
        for path in (Path(f"{stem}.png"), Path(f"{stem}_color.png")):
            if path.exists():
                with Image.open(path) as page:
                    page.load()
                self._draw_header(page, script)
                page.save(path)
                if page.mode == "1":
                    Path(f"{stem}.bin").write_bytes(page.tobytes())

    def _draw_panels(self, canvas, layout_type, panels, left, top, area_w, area_h, h) -> List[Optional[Image.Image]]:
        """Draws every panel; returns each panel's original model art (None for procedural art)."""
        originals = []
        for i, quad in enumerate(LAYOUTS[layout_type][1]):
            panel_data = panels[i] if i < len(panels) else {}
            pts = [(left + fx * area_w, top + fy * area_h) for fx, fy in quad]
            outer = Image.new("L", (WIDTH, HEIGHT), 0)
            ImageDraw.Draw(outer).polygon(pts, fill=255)
            canvas.paste((255, 255, 255), mask=outer)
            inner = outer.filter(ImageFilter.MinFilter(self.gutter + 1))
            # Text goes in the largest upright box inside the quad, so slanted edges don't clip it
            (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = pts
            text_box = tuple(round(v) for v in (max(tlx, blx) + h, max(tly, try_) + h,
                                                min(trx, brx) - h, min(bly, bry) - h))
            layer = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
            originals.append(self._render_panel(layer, ImageDraw.Draw(layer), inner.getbbox(), text_box, panel_data, i))
            canvas.paste(layer, mask=inner)
            canvas.paste((0, 0, 0), mask=ImageChops.subtract(inner, inner.filter(ImageFilter.MinFilter(7))))
        return originals

    def _render_panel(self, canvas: Image.Image, draw: ImageDraw.ImageDraw,
                      box: Tuple[int, int, int, int], text_box: Tuple[int, int, int, int],
                      panel_data: Dict[str, Any], index: int) -> Optional[Image.Image]:
        """Render illustration (filling box), then narrator box, speech balloons, and SFX (inside text_box).
        The caller clips to the panel shape and draws the frame. Returns the model's original art."""
        x0, y0, x1, y1 = box
        p_width = x1 - x0
        p_height = y1 - y0

        # 1. Generate rich panel illustration with FLUX.1 (or procedural fallback)
        from server.render.flux_generator import flux_generator
        scene_desc = panel_data.get("scene_description", "")
        flux_img = flux_generator.generate_panel_art(scene_desc, (p_width, p_height))
        
        if flux_img:
            canvas.paste(flux_img.resize((p_width, p_height), Image.Resampling.LANCZOS), (x0, y0))
        else:
            self._render_comic_art(draw, box, panel_data, index)
        
        # Text placement uses the upright box that fits inside the panel shape
        x0, y0, x1, y1 = text_box
        p_width = x1 - x0

        # Panel Number tag in bottom right
        tag_font = _get_font(10, bold=True)
        draw.text((x1 - 18, y1 - 16), f"P{index + 1}", fill=(0, 0, 0), font=tag_font)
        
        # Render Narration box if present
        narration = panel_data.get("narration")
        balloon_top_offset = y0 + 10
        if narration:
            balloon_top_offset = self._draw_narration_box(draw, x0 + 8, y0 + 8, p_width - 16, narration)
            
        # Render Speech Balloons
        balloons = panel_data.get("speech_balloons", [])
        for b_idx, balloon in enumerate(balloons):
            b_text = balloon.get("text", "")
            b_char = balloon.get("character", "")
            if b_text:
                full_text = f"{b_char.upper()}: {b_text}" if b_char else b_text
                # Alternate balloon positions if multiple
                b_x = x0 + 12 if b_idx % 2 == 0 else x0 + (p_width // 4)
                b_y = balloon_top_offset + (b_idx * 55)
                max_w = min(p_width - 24, 220)
                self._draw_speech_balloon(draw, b_x, b_y, max_w, full_text, points_left=(b_idx % 2 == 0))
                
        # Render Sound Effect if present
        sfx = panel_data.get("sound_effect")
        if sfx:
            sfx_font = _get_font(20, bold=True)
            sfx_x = x0 + (p_width // 2) - 30
            sfx_y = y1 - 45
            # Draw comic drop shadow
            draw.text((sfx_x + 2, sfx_y + 2), sfx.upper(), fill=(0, 0, 0), font=sfx_font)
            draw.text((sfx_x, sfx_y), sfx.upper(), fill=(0, 0, 0), font=sfx_font)
        return flux_img

    def _render_comic_art(self, draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int],
                          panel_data: Dict[str, Any], index: int):
        """
        Procedural comic line-art artist:
        Draws dynamic comic silhouettes, horizon, halftone textures, speed lines,
        and expressive character profiles suited for monochrome e-ink.
        """
        x0, y0, x1, y1 = box
        pw = x1 - x0
        ph = y1 - y0
        
        # Draw comic background speed lines or horizon
        horizon_y = y0 + int(ph * 0.65)
        draw.line([(x0, horizon_y), (x1, horizon_y)], fill=(0, 0, 0), width=2)
        
        # Halftone ground pattern
        for gy in range(horizon_y + 8, y1 - 4, 8):
            draw.line([(x0, gy), (x1, gy)], fill=(180, 180, 180), width=1)
            
        # Draw expressive comic character silhouette based on panel index
        if index % 2 == 0:
            # Silhouette 1: Dramatic trenchcoat / suited figure looking right
            cx = x0 + int(pw * 0.3)
            cy = horizon_y
            # Body / Torso
            draw.polygon([(cx - 20, cy), (cx + 25, cy), (cx + 15, cy - 70), (cx - 15, cy - 70)], fill=(0, 0, 0))
            # Head with fedora / hairstyle
            draw.ellipse([cx - 14, cy - 95, cx + 14, cy - 67], fill=(0, 0, 0))
            draw.line([(cx - 25, cy - 88), (cx + 25, cy - 88)], fill=(0, 0, 0), width=4) # Hat brim
            # Comic dramatic motion line
            draw.line([(cx + 20, cy - 50), (cx + 45, cy - 40)], fill=(0, 0, 0), width=2)
        else:
            # Silhouette 2: Inquisitive scientist / alien / robot holding an object
            cx = x0 + int(pw * 0.65)
            cy = horizon_y
            # Angular robot/cyborg body
            draw.rectangle([cx - 22, cy - 75, cx + 22, cy], fill=(0, 0, 0))
            # Screen / eye visor
            draw.ellipse([cx - 16, cy - 100, cx + 16, cy - 68], fill=(0, 0, 0))
            draw.rectangle([cx - 8, cy - 88, cx + 8, cy - 82], fill=(255, 255, 255)) # Visor
            # Held strange object (the 'trombone / spatula')
            draw.polygon([(cx - 35, cy - 40), (cx - 20, cy - 55), (cx - 15, cy - 45)], fill=(0, 0, 0))
            # Radiating energy lines around object
            draw.line([(cx - 42, cy - 42), (cx - 48, cy - 45)], fill=(0, 0, 0), width=2)
            draw.line([(cx - 30, cy - 62), (cx - 33, cy - 68)], fill=(0, 0, 0), width=2)

    def _draw_narration_box(self, draw: ImageDraw.ImageDraw, x: int, y: int, max_width: int, text: str) -> int:
        """Draw a rectangular narrator caption box with drop shadow."""
        font = _get_font(11, bold=True)
        lines = wrap_text(text, font, max_width - 12, draw)
        if not lines:
            return y
        
        line_height = 14
        box_h = (len(lines) * line_height) + 10
        box_w = max_width
        
        # Drop shadow
        draw.rectangle([x + 3, y + 3, x + box_w + 3, y + box_h + 3], fill=(0, 0, 0))
        # Main caption box
        draw.rectangle([x, y, x + box_w, y + box_h], fill=(255, 255, 255), outline=(0, 0, 0), width=2)
        
        for i, line in enumerate(lines):
            draw.text((x + 6, y + 5 + i * line_height), line, fill=(0, 0, 0), font=font)
            
        return y + box_h + 10

    def _draw_speech_balloon(self, draw: ImageDraw.ImageDraw, x: int, y: int,
                             max_width: int, text: str, points_left: bool = True):
        """Draw a classic rounded comic speech balloon with directional tail."""
        font = _get_font(11, bold=False)
        lines = wrap_text(text, font, max_width - 16, draw)
        if not lines:
            return
            
        line_height = 14
        box_h = (len(lines) * line_height) + 12
        # Calculate actual width
        max_line_w = max(draw.textbbox((0, 0), line, font=font)[2] for line in lines)
        box_w = min(max_width, max(max_line_w + 16, 70))
        
        # Rounded balloon
        draw.rounded_rectangle([x, y, x + box_w, y + box_h], radius=7, fill=(255, 255, 255), outline=(0, 0, 0), width=2)
        
        # Speech tail pointing down
        if points_left:
            tail = [(x + 12, y + box_h - 1), (x + 6, y + box_h + 10), (x + 22, y + box_h - 1)]
        else:
            tail = [(x + box_w - 22, y + box_h - 1), (x + box_w - 6, y + box_h + 10), (x + box_w - 12, y + box_h - 1)]
            
        draw.polygon(tail, fill=(255, 255, 255))
        draw.line([tail[0], tail[1], tail[2]], fill=(0, 0, 0), width=2)
        # Erase internal line between balloon and tail
        draw.line([(tail[0][0] + 1, y + box_h), (tail[2][0] - 1, y + box_h)], fill=(255, 255, 255), width=2)
        
        # Render text
        for i, line in enumerate(lines):
            draw.text((x + 8, y + 6 + i * line_height), line, fill=(0, 0, 0), font=font)


comic_compositor = ComicCompositor()
