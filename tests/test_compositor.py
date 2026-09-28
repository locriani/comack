import os
import unittest
from server.render.comic_compositor import comic_compositor, LAYOUTS, resolve_layout
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

    def test_resolve_layout(self):
        self.assertEqual(resolve_layout("action", 4), "action")
        # Mismatch or unknown -> the plain layout for that count, never random
        self.assertEqual(resolve_layout("action", 2), "two-columns")
        self.assertEqual(resolve_layout("2-panel", 3), "three-columns")
        self.assertEqual(resolve_layout("", 0), "splash")
        self.assertEqual(resolve_layout("", 9), "five")


if __name__ == "__main__":
    unittest.main()
