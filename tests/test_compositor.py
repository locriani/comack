import os
import unittest
from unittest.mock import patch
from server.render.comic_compositor import comic_compositor, LAYOUTS, resolve_layout
from server.render.flux_generator import flux_generator
from PIL import Image


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

    def test_originals_are_saved(self):
        art = Image.new("RGB", (1024, 480), (200, 30, 30))   # the model's own resolution, in color
        script = {"issue_number": 998, "title": "Red", "layout_type": "two-columns", "panels": [panel(1), panel(2)]}
        with patch.object(flux_generator, "generate_panel_art", return_value=art):
            png_path, bin_path = comic_compositor.compose_comic(script)
        files = [png_path, bin_path] + [png_path.with_name(f"issue_0998_{s}.png") for s in ("panel1", "panel2", "color")]
        try:
            with Image.open(files[2]) as original:
                self.assertEqual((original.size, original.getpixel((0, 0))), ((1024, 480), (200, 30, 30)))
            with Image.open(files[4]) as page:   # the pre-halftone page keeps its color
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
            for f in (png_path, bin_path, color):
                f.unlink()

    def test_resolve_layout(self):
        self.assertEqual(resolve_layout("action", 4), "action")
        # Mismatch or unknown -> the plain layout for that count, never random
        self.assertEqual(resolve_layout("action", 2), "two-columns")
        self.assertEqual(resolve_layout("2-panel", 3), "three-columns")
        self.assertEqual(resolve_layout("", 0), "splash")
        self.assertEqual(resolve_layout("", 9), "five")


if __name__ == "__main__":
    unittest.main()
