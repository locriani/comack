import json
import unittest
from server.story.narrative_engine import story_so_far


class TestStorySoFar(unittest.TestCase):
    def test_includes_all_panel_text_in_order(self):
        comics = [
            {"issue_number": 1, "title": "Pilot", "script_json": json.dumps({"panels": [
                {"narration": "Meanwhile...", "speech_balloons": [{"character": "Banjo", "text": "Hello."}],
                 "sound_effect": "KLANG"}]})},
            {"issue_number": 2, "title": "Sequel", "script_json": json.dumps({"panels": [
                {"narration": None, "speech_balloons": None}]})},
        ]
        out = story_so_far(comics)
        for s in ["Issue #1: Pilot", "[Caption] Meanwhile...", "Banjo: Hello.", "[SFX] KLANG", "Issue #2: Sequel"]:
            self.assertIn(s, out)
        self.assertLess(out.index("Pilot"), out.index("Sequel"))
        self.assertEqual(story_so_far([]), "")


if __name__ == "__main__":
    unittest.main()
