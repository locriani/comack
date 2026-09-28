"""
800x480 Comic Page Compositor and E-Paper Dithering Engine.
Assembles 1-, 2-, or 3-panel comic strips, renders speech balloons, narrator boxes,
and sound effects, then applies Floyd-Steinberg error-diffusion dithering.
Outputs both PNG preview and 48,000-byte 1-bit packed binary for the reTerminal E1001.
"""
import math
import numpy as np
from pathlib import Path
from typing import Dict, Any, List, Tuple
from PIL import Image, ImageDraw, ImageFont
from server.config import settings, COMICS_DIR

WIDTH = settings.DISPLAY_WIDTH   # 800
HEIGHT = settings.DISPLAY_HEIGHT # 480


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
    
    # Pack 800x480 1-bit image into raw bytes (1 bit per pixel, 100 bytes per row)
    # EPD convention: 1 = White, 0 = Black
    img_array = np.array(dithered, dtype=np.uint8) # 255 for white, 0 for black
    packed_bytes = bytearray()
    
    for y in range(HEIGHT):
        row = img_array[y]
        for x in range(0, WIDTH, 8):
            byte_val = 0
            for bit in range(8):
                if x + bit < WIDTH:
                    pixel = row[x + bit]
                    if pixel > 128: # White pixel -> 1
                        byte_val |= (1 << (7 - bit))
            packed_bytes.append(byte_val)
            
    return dithered, bytes(packed_bytes)


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
        title = script.get("title", f"Issue #{issue_number}")
        layout_type = script.get("layout_type", "2-panel")
        panels = script.get("panels", [])
        
        # Base canvas in RGB
        canvas = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)
        
        # 1. Render Top Header Banner
        draw.rectangle([0, 0, WIDTH, self.banner_height], fill=(0, 0, 0))
        header_font = _get_font(14, bold=True)
        header_text = f"COMACK  •  ISSUE #{issue_number:03d}  •  {title.upper()}"
        draw.text((self.margin, 6), header_text, fill=(255, 255, 255), font=header_font)
        
        # 2. Compute panel bounding boxes
        panel_area_top = self.banner_height + self.margin
        panel_area_height = HEIGHT - panel_area_top - self.margin
        num_panels = len(panels) if panels else 1
        
        if num_panels == 1:
            panel_boxes = [
                (self.margin, panel_area_top, WIDTH - self.margin, panel_area_top + panel_area_height)
            ]
        elif num_panels == 2:
            panel_width = (WIDTH - (2 * self.margin) - self.gutter) // 2
            panel_boxes = [
                (self.margin, panel_area_top, self.margin + panel_width, panel_area_top + panel_area_height),
                (self.margin + panel_width + self.gutter, panel_area_top, WIDTH - self.margin, panel_area_top + panel_area_height)
            ]
        else: # 3 panels
            panel_width = (WIDTH - (2 * self.margin) - (2 * self.gutter)) // 3
            panel_boxes = [
                (self.margin + i * (panel_width + self.gutter),
                 panel_area_top,
                 self.margin + i * (panel_width + self.gutter) + panel_width,
                 panel_area_top + panel_area_height)
                for i in range(3)
            ]
            
        # 3. Draw each panel
        for i, box in enumerate(panel_boxes):
            panel_data = panels[i] if i < len(panels) else {}
            self._render_panel(canvas, draw, box, panel_data, i, num_panels)
            
        # 4. Dither and Export
        dithered_img, bin_bytes = dither_to_1bit_monochrome(canvas)
        
        # Ensure 48,000 bytes exactly
        assert len(bin_bytes) == (WIDTH * HEIGHT // 8), f"Bin size must be 48000, got {len(bin_bytes)}"
        
        issue_str = f"issue_{issue_number:04d}"
        png_path = COMICS_DIR / f"{issue_str}.png"
        bin_path = COMICS_DIR / f"{issue_str}.bin"
        current_png = COMICS_DIR / "current.png"
        current_bin = COMICS_DIR / "current.bin"
        
        # Save numbered issue
        dithered_img.save(png_path)
        with open(bin_path, "wb") as f:
            f.write(bin_bytes)
            
        # Update current pointers
        dithered_img.save(current_png)
        with open(current_bin, "wb") as f:
            f.write(bin_bytes)
            
        return png_path, bin_path

    def _render_panel(self, canvas: Image.Image, draw: ImageDraw.ImageDraw,
                      box: Tuple[int, int, int, int], panel_data: Dict[str, Any],
                      index: int, total_panels: int):
        """Render illustration, borders, narrator box, speech balloons, and SFX in a panel box."""
        x0, y0, x1, y1 = box
        p_width = x1 - x0
        p_height = y1 - y0
        
        # Fill panel background
        draw.rectangle([x0, y0, x1, y1], fill=(255, 255, 255))
        
        # 1. Generate rich panel illustration with FLUX.1 (or procedural fallback)
        from server.render.flux_generator import flux_generator
        scene_desc = panel_data.get("scene_description", "")
        flux_img = flux_generator.generate_panel_art(scene_desc, (p_width, p_height))
        
        if flux_img:
            canvas.paste(flux_img.convert("RGB"), (x0, y0))
        else:
            self._render_comic_art(draw, box, panel_data, index)
        
        # Render thick classic comic frame border
        border_width = 3
        draw.rectangle([x0, y0, x1, y1], outline=(0, 0, 0), width=border_width)
        
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
