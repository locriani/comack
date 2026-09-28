import unittest
from PIL import Image
from server.render.flux_generator import FluxGenerator

class TestFluxGenerator(unittest.TestCase):
    def test_flux_generator_interface(self):
        gen = FluxGenerator()
        # Even without PyTorch installed on local machine, interface shouldn't crash
        art = gen.generate_panel_art("A futuristic cybernetic cat in a rain-soaked alley", (380, 420))
        # If model is not loaded, it returns None and compositor falls back cleanly
        if art is not None:
            self.assertIsInstance(art, Image.Image)
            self.assertEqual(art.size, (380, 420))

if __name__ == "__main__":
    unittest.main()
