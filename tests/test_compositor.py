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

    def test_resolve_layout(self):
        self.assertEqual(resolve_layout("action", 4), "action")
        # Mismatch or unknown -> the plain layout for that count, never random
        self.assertEqual(resolve_layout("action", 2), "two-columns")
        self.assertEqual(resolve_layout("2-panel", 3), "three-columns")
        self.assertEqual(resolve_layout("", 0), "splash")
        self.assertEqual(resolve_layout("", 9), "five")


if __name__ == "__main__":
    unittest.main()
