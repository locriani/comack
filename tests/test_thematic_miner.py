import unittest
from server.privacy.thematic_miner import mine_story_elements

class TestThematicMiner(unittest.TestCase):
    def test_mine_story_elements_no_leakage(self):
        secret_speech = "My secret password is orange-kangaroo and we are late for the meeting"
        elements = mine_story_elements(secret_speech, duration_seconds=3.5)
        
        # Verify keys exist
        self.assertIn("emotional_tone", elements)
        self.assertIn("abstract_theme", elements)
        self.assertIn("surreal_metaphor", elements)
        self.assertIn("energy_level", elements)
        
        # Verify secret phrases NEVER appear in output elements
        for val in elements.values():
            if isinstance(val, str):
                self.assertNotIn("secret password", val)
                self.assertNotIn("orange-kangaroo", val)

    def test_tone_detection(self):
        urgent_speech = "Hurry up, we have an emergency deadline right now!"
        elements = mine_story_elements(urgent_speech, duration_seconds=2.0)
        self.assertEqual(elements["emotional_tone"], "urgent")

if __name__ == "__main__":
    unittest.main()
