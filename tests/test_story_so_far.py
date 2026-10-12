import json
import unittest
from server.story.narrative_engine import story_so_far


class TestStorySoFar(unittest.TestCase):
    def test_includes_all_panel_text_in_order(self):
        comics = [
            {"issue_number": 1, "arc_id": 7, "title": "Pilot", "script_json": json.dumps({"panels": [
                {"narration": "Meanwhile...", "speech_balloons": [{"character": "Banjo", "text": "Hello."}],
                 "sound_effect": "KLANG"}]})},
            {"issue_number": 2, "arc_id": 7, "title": "Sequel", "script_json": json.dumps({"panels": [
                {"narration": None, "speech_balloons": None}]})},
        ]
        out = story_so_far(comics, arc_id=7)
        for s in ["Issue #1: Pilot", "[Caption] Meanwhile...", "Banjo: Hello.", "[SFX] KLANG", "Issue #2: Sequel"]:
            self.assertIn(s, out)
        self.assertLess(out.index("Pilot"), out.index("Sequel"))
        self.assertEqual(story_so_far([]), "")

    def test_drops_comics_from_other_arcs(self):
        comics = [
            {"issue_number": 8, "arc_id": 1, "title": "Old Arc Finale",
             "script_json": json.dumps({"panels": [{"narration": "The beige singularity ends.",
                                                   "speech_balloons": []}]})},
            {"issue_number": 43, "arc_id": 7, "title": "Celestial Pilot",
             "script_json": json.dumps({"panels": [{"narration": "Cloud-seas stretch ahead.",
                                                   "speech_balloons": []}]})},
        ]
        out = story_so_far(comics, arc_id=7)
        self.assertIn("Celestial Pilot", out)
        self.assertIn("Cloud-seas stretch ahead.", out)
        self.assertNotIn("Old Arc Finale", out)
        self.assertNotIn("beige singularity", out)


if __name__ == "__main__":
    unittest.main()
