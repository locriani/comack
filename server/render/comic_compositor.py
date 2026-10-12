"""
800x480 Comic Page Compositor and E-Paper Dithering Engine.
Assembles comic pages from common layouts (LAYOUTS), renders speech balloons, narrator boxes,
and sound effects, then applies Floyd-Steinberg error-diffusion dithering.
Outputs both PNG preview and 48,000-byte 1-bit packed binary for the reTerminal E1001.
"""
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image, ImageChops, ImageDraw, ImageFilter
from server.config import settings, COMICS_DIR
from server.layouts import LAYOUTS, resolve_layout
from server.render.dither_spectra6 import dither_spectra6
from server.render.lettering import draw_lettering, get_font
from server.render.panel_prompt import normalize_text_anchor

WIDTH = settings.DISPLAY_WIDTH   # 800
HEIGHT = settings.DISPLAY_HEIGHT # 480


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
        art_style = script.get("art_style") or ""
        try:
            originals = self._draw_panels(
                canvas, layout_type, panels, left, top, area_w, area_h, h, art_style=art_style)
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
        self.write_color_bin(issue_number)
        return png_path, bin_path

    def write_color_bin(self, issue_number: int) -> Path:
        """The 192,000-byte Spectra 6 image (4 bits per pixel) for the E1002, dithered from the saved
        full-color page, else from the 1-bit page for issues that predate it."""
        stem = COMICS_DIR / f"issue_{issue_number:04d}"
        source = next((p for p in (Path(f"{stem}_color.png"), Path(f"{stem}.png")) if p.exists()), None)
        if source is None:
            raise FileNotFoundError(f"Issue #{issue_number} has no page to dither")
        with Image.open(source) as page:
            _, packed = dither_spectra6(page)
        assert len(packed) == WIDTH * HEIGHT // 2, f"Color bin must be 192000, got {len(packed)}"
        out = Path(f"{stem}_color.bin")
        out.write_bytes(packed)
        return out

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
        header_font = get_font(size, bold=True)
        while size > 8 and draw.textlength(header_text, font=header_font) > WIDTH - 2 * self.margin:
            size -= 1  # shrink long titles to fit the banner
            header_font = get_font(size, bold=True)
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
        if Path(f"{stem}_color.png").exists():
            self.write_color_bin(script["issue_number"])

    def _draw_panels(self, canvas, layout_type, panels, left, top, area_w, area_h, h,
                     art_style: str = "") -> List[Optional[Image.Image]]:
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
            originals.append(self._render_panel(
                layer, ImageDraw.Draw(layer), inner.getbbox(), text_box, panel_data, i,
                art_style=art_style))
            canvas.paste(layer, mask=inner)
            canvas.paste((0, 0, 0), mask=ImageChops.subtract(inner, inner.filter(ImageFilter.MinFilter(7))))
        return originals

    def _render_panel(self, canvas: Image.Image, draw: ImageDraw.ImageDraw,
                      box: Tuple[int, int, int, int], text_box: Tuple[int, int, int, int],
                      panel_data: Dict[str, Any], index: int,
                      art_style: str = "") -> Optional[Image.Image]:
        """Render illustration (filling box), then narrator box, speech balloons, and SFX (inside text_box).
        The caller clips to the panel shape and draws the frame. Returns the model's original art."""
        x0, y0, x1, y1 = box
        p_width = x1 - x0
        p_height = y1 - y0
        anchor = normalize_text_anchor(panel_data.get("text_anchor"))

        # 1. Generate rich panel illustration with FLUX.1 (or procedural fallback)
        from server.render.flux_generator import flux_generator
        scene_desc = panel_data.get("scene_description", "")
        flux_img = flux_generator.generate_panel_art(
            scene_desc, (p_width, p_height), art_style=art_style, text_anchor=anchor)

        if flux_img:
            canvas.paste(flux_img.resize((p_width, p_height), Image.Resampling.LANCZOS), (x0, y0))
        else:
            self._render_comic_art(draw, box, panel_data, index)

        # Lettering stays in the upright box that fits inside the panel shape
        x0, y0, x1, y1 = text_box
        p_width = x1 - x0
        p_height = y1 - y0

        tag_font = get_font(10, bold=True)
        draw.text((x1 - 18, y1 - 16), f"P{index + 1}", fill=(0, 0, 0), font=tag_font)

        draw_lettering(draw, text_box, panel_data, anchor)
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


comic_compositor = ComicCompositor()
