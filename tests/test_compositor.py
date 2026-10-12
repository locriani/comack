import os
import unittest
from unittest.mock import patch
from server.layouts import LAYOUTS, resolve_layout
from server.render.comic_compositor import comic_compositor, fit_cover
from server.render.flux_generator import flux_generator
from PIL import Image, ImageDraw


def panel(n):
    return {
        "panel_number": n,
        "scene_description": "Detective examining a weird glowing turnip.",
        "narration": "Meanwhile in Sector 4B..." if n == 1 else None,
        "speech_balloons": [{"character": "Agent Banjo", "text": "Is that what I think it is?"}],
        "sound_effect": "HUMMM",
    }


class TestComicCompositor(unittest.TestCase):
    def test_every_layout_composes(self):
        for name, (_, quads) in LAYOUTS.items():
            with self.subTest(layout=name):
                script = {"issue_number": 999, "title": "The Quantum Turnip", "layout_type": name,
                          "panels": [panel(n + 1) for n in range(len(quads))]}
                png_path, bin_path = comic_compositor.compose_comic(script)
                with Image.open(png_path) as img:
                    self.assertEqual(img.size, (800, 480))
                # EXACTLY 48,000 bytes (800 * 480 / 8)
                self.assertEqual(os.path.getsize(bin_path), 48000)
                # Bits must carry the image (1 = white): a mostly-white comic can't pack to all zeros
                data = bin_path.read_bytes()
                self.assertGreater(sum(bin(b).count("1") for b in data), 800 * 480 // 2)
                png_path.unlink()
                bin_path.unlink()
                png_path.with_name("issue_0999_color.png").unlink()
                color_bin = png_path.with_name("issue_0999_color.bin")
                self.assertEqual(os.path.getsize(color_bin), 192000)  # 4 bits per pixel
                color_bin.unlink()

    def test_originals_are_saved(self):
        art = Image.new("RGB", (1024, 480), (200, 30, 30))   # the model's own resolution, in color
        script = {"issue_number": 998, "title": "Red", "layout_type": "two-columns", "panels": [panel(1), panel(2)]}
        with patch.object(flux_generator, "generate_panel_art", return_value=art):
            png_path, bin_path = comic_compositor.compose_comic(script)
        files = [png_path, bin_path, png_path.with_name("issue_0998_color.bin")] + [
            png_path.with_name(f"issue_0998_{s}.png") for s in ("panel1", "panel2", "color")]
        try:
            with Image.open(files[3]) as original:
                self.assertEqual((original.size, original.getpixel((0, 0))), ((1024, 480), (200, 30, 30)))
            with Image.open(files[5]) as page:   # the pre-halftone page keeps its color
                self.assertIn((200, 30, 30), [c for _, c in page.getcolors(1 << 16)])
        finally:
            for f in files:
                f.unlink()

    def test_originals_are_served_and_nothing_else(self):
        from fastapi.testclient import TestClient
        from server.config import COMICS_DIR
        from server.main import app
        client = TestClient(app)
        color = COMICS_DIR / "issue_0996_color.png"
        Image.new("RGB", (8, 8), (1, 2, 3)).save(color)
        try:
            self.assertEqual(client.get("/api/comics/996/color.png").status_code, 200)
            for bad in ("/api/comics/996/panel1.png", "/api/comics/996/bin.png",
                        "/api/comics/996/..%2Fissue_0996_color.png"):
                self.assertEqual(client.get(bad).status_code, 404, bad)
        finally:
            color.unlink()

    def test_redraw_header_changes_only_the_banner(self):
        script = {"issue_number": 997, "title": "Hdr", "layout_type": "splash", "panels": [panel(1)]}
        png_path, bin_path = comic_compositor.compose_comic(script)
        color = png_path.with_name("issue_0997_color.png")
        try:
            with Image.open(png_path) as before:
                before = before.copy()
            comic_compositor.redraw_header({**script, "series_issue": 1})
            with Image.open(png_path) as after:
                self.assertEqual(after.mode, "1")
                banner = (0, 0, 800, comic_compositor.banner_height + 1)
                self.assertNotEqual(before.crop(banner).tobytes(), after.crop(banner).tobytes())
                below = (0, comic_compositor.banner_height + 1, 800, 480)
                self.assertEqual(before.crop(below).tobytes(), after.crop(below).tobytes())
                self.assertEqual(bin_path.read_bytes(), after.tobytes())
        finally:
            for f in (png_path, bin_path, color, color.with_suffix(".bin")):
                f.unlink()

    def test_color_bin_is_served_and_made_on_demand(self):
        from fastapi.testclient import TestClient
        from server.config import COMICS_DIR
        from server.main import app
        client = TestClient(app)
        color = COMICS_DIR / "issue_0995_color.png"
        Image.new("RGB", (800, 480), (200, 30, 30)).save(color)
        try:
            reply = client.get("/api/comics/995/color.bin")   # no bin yet: made from the saved page
            self.assertEqual((reply.status_code, len(reply.content)), (200, 192000))
            part = client.get("/api/comics/995/color.bin/0")
            self.assertEqual((part.status_code, len(part.content)), (200, 48000))
            self.assertEqual(part.content, reply.content[:48000])
            self.assertEqual(client.get("/api/comics/995/color.bin/4").status_code, 404)
            self.assertEqual(client.get("/api/comics/994/color.bin").status_code, 404)
        finally:
            color.unlink()
            color.with_suffix(".bin").unlink(missing_ok=True)

    def test_resolve_layout(self):
        self.assertEqual(resolve_layout("action", 4), "action")
        # Mismatch or unknown -> the plain layout for that count, never random
        self.assertEqual(resolve_layout("action", 2), "two-columns")
        self.assertEqual(resolve_layout("2-panel", 3), "three-columns")
        self.assertEqual(resolve_layout("", 0), "splash")
        self.assertEqual(resolve_layout("", 9), "six-grid")
        self.assertEqual(resolve_layout("six-strip", 6), "six-strip")

    def test_lettering_honors_text_anchor(self):
        """Captions park in the text_anchor band so they sit on clear art space."""
        from server.render.lettering import draw_lettering
        box = (100, 100, 400, 400)
        x0, y0, x1, y1 = box
        mx, my = (x0 + x1) // 2, (y0 + y1) // 2

        def white_in(layer, region):
            rx0, ry0, rx1, ry1 = region
            n = 0
            for y in range(ry0, ry1, 2):
                for x in range(rx0, rx1, 2):
                    if layer.getpixel((x, y))[0] > 240:
                        n += 1
            return n

        # top-left lettering should put white caption pixels in the NW quadrant, not SE
        layer = Image.new("RGB", (800, 480), (180, 180, 180))
        draw_lettering(ImageDraw.Draw(layer), box, {
            "narration": "Meanwhile in the quiet archive…",
            "speech_balloons": [{"character": "Pip", "text": "Hello there friend."}],
        }, "top-left")
        self.assertGreater(white_in(layer, (x0, y0, mx, my)), white_in(layer, (mx, my, x1, y1)))

        layer = Image.new("RGB", (800, 480), (180, 180, 180))
        draw_lettering(ImageDraw.Draw(layer), box, {
            "narration": "Meanwhile in the quiet archive…",
            "speech_balloons": [{"character": "Pip", "text": "Hello there friend."}],
        }, "bottom-right")
        self.assertGreater(white_in(layer, (mx, my, x1, y1)), white_in(layer, (x0, y0, mx, my)))

    def test_compose_passes_script_art_style_to_flux(self):
        art = Image.new("RGB", (64, 64), (10, 20, 30))
        script = {
            "issue_number": 994,
            "title": "Styled",
            "layout_type": "splash",
            "art_style": "rubber-hose cartoon",
            "panels": [panel(1)],
        }
        with patch.object(flux_generator, "generate_panel_art", return_value=art) as gen:
            png_path, bin_path = comic_compositor.compose_comic(script)
        try:
            self.assertTrue(gen.called)
            self.assertEqual(gen.call_args.kwargs.get("art_style"), "rubber-hose cartoon")
        finally:
            for f in (png_path, bin_path, png_path.with_name("issue_0994_color.png"),
                      png_path.with_name("issue_0994_color.bin"),
                      png_path.with_name("issue_0994_panel1.png")):
                f.unlink(missing_ok=True)

    def test_fit_cover_preserves_aspect_and_fills_box(self):
        # Wide red art into a square: scale by height, center-crop sides — still red, exact size.
        art = Image.new("RGB", (200, 100), (220, 40, 40))
        out = fit_cover(art, (100, 100))
        self.assertEqual(out.size, (100, 100))
        self.assertEqual(out.getpixel((50, 50)), (220, 40, 40))
        # Tall blue art into a wide box: scale by width, crop top/bottom.
        tall = Image.new("RGB", (50, 200), (40, 40, 220))
        wide = fit_cover(tall, (100, 50))
        self.assertEqual(wide.size, (100, 50))
        self.assertEqual(wide.getpixel((50, 25)), (40, 40, 220))

    def test_compose_reuses_panel_arts_without_flux(self):
        art = Image.new("RGB", (300, 100), (10, 200, 30))  # wide — must not be stretched into a tall box
        script = {
            "issue_number": 993,
            "title": "Reuse",
            "layout_type": "two-columns",
            "panels": [panel(1), panel(2)],
        }
        with patch.object(flux_generator, "generate_panel_art") as gen:
            png_path, bin_path = comic_compositor.compose_comic(
                script, panel_arts=[art, art])
        try:
            gen.assert_not_called()
            self.assertTrue(png_path.exists())
        finally:
            for f in (png_path, bin_path, png_path.with_name("issue_0993_color.png"),
                      png_path.with_name("issue_0993_color.bin"),
                      png_path.with_name("issue_0993_panel1.png"),
                      png_path.with_name("issue_0993_panel2.png")):
                f.unlink(missing_ok=True)

if __name__ == "__main__":
    unittest.main()
