import unittest
from unittest.mock import patch, MagicMock
from io import BytesIO
from PIL import Image
import simulator.reterminal_sim as sim


class TestSimulatorColorSupport(unittest.TestCase):
    def test_fetch_current_image_color(self):
        # In color mode, attempts color URLs first
        called_urls = []
        col_img = Image.new("RGB", (800, 480), (255, 0, 0))
        buf = BytesIO()
        col_img.save(buf, format="PNG")
        buf.seek(0)
        img_bytes = buf.getvalue()

        def fake_urlopen(url, timeout=4):
            called_urls.append(url)
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value.read.return_value = img_bytes
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            # Simulate the fetch_current_image logic from run_gui_simulator with is_color=True
            server_base = "http://localhost:8000"
            is_color = True
            urls = [
                f"{server_base}/api/comics/current_color.png?t=123",
                f"{server_base}/api/comics/current.png?color=true&t=123",
                f"{server_base}/api/comics/current.png?t=123"
            ]
            img = None
            for url in urls:
                try:
                    with fake_urlopen(url, timeout=4) as resp:
                        img = Image.open(BytesIO(resp.read())).convert("RGB")
                        break
                except Exception:
                    continue

            self.assertIsNotNone(img)
            self.assertEqual(img.size, (800, 480))
            self.assertIn("current_color.png", called_urls[0])

    def test_fetch_current_image_bw(self):
        called_urls = []
        bw_img = Image.new("1", (800, 480), 1)
        buf = BytesIO()
        bw_img.save(buf, format="PNG")
        buf.seek(0)
        img_bytes = buf.getvalue()

        def fake_urlopen(url, timeout=4):
            called_urls.append(url)
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value.read.return_value = img_bytes
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            server_base = "http://localhost:8000"
            url = f"{server_base}/api/comics/current.png?t=123"
            with fake_urlopen(url, timeout=4) as resp:
                img = Image.open(BytesIO(resp.read())).convert("RGB")

            self.assertEqual(img.size, (800, 480))
            self.assertEqual(called_urls, [url])


if __name__ == "__main__":
    unittest.main()
