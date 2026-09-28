import os
import unittest
from pathlib import Path
from server.render.comic_compositor import comic_compositor, dither_to_1bit_monochrome
from PIL import Image

class TestComicCompositor(unittest.TestCase):
    def test_comic_compositor_output(self):
        mock_script = {
            "issue_number": 999,
            "title": "The Quantum Turnip",
            "layout_type": "2-panel",
            "panel_count": 2,
            "panels": [
                {
                    "panel_number": 1,
                    "scene_description": "Detective examining a weird glowing turnip.",
                    "narration": "Meanwhile in Sector 4B...",
                    "speech_balloons": [
                        {"character": "Agent Banjo", "text": "Is that what I think it is?"}
                    ],
                    "sound_effect": "HUMMM"
                },
                {
                    "panel_number": 2,
                    "scene_description": "Close up of turnip answering back.",
                    "narration": None,
                    "speech_balloons": [
                        {"character": "Turnip", "text": "I demand an attorney!"}
                    ],
                    "sound_effect": "BZZZZT"
                }
            ]
        }
        
        png_path, bin_path = comic_compositor.compose_comic(mock_script)
        
        # 1. Verify PNG exists and has correct 800x480 resolution
        self.assertTrue(png_path.exists())
        with Image.open(png_path) as img:
            self.assertEqual(img.size, (800, 480))
        
        # 2. Verify BIN exists and is EXACTLY 48,000 bytes (800 * 480 / 8)
        self.assertTrue(bin_path.exists())
        file_size = os.path.getsize(bin_path)
        self.assertEqual(file_size, 48000, f"Expected 48000 bytes for 800x480 e-paper, got {file_size}")
        
        # Clean up test files
        if png_path.exists(): png_path.unlink()
        if bin_path.exists(): bin_path.unlink()

if __name__ == "__main__":
    unittest.main()
