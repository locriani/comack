import unittest
from server.config import settings
from server.main import comic_due
from server.story.narrative_engine import lines_wanted
from server.story.transcript_batch import parse_lines_wanted, select_transcript_batch


class TestPacing(unittest.TestCase):
    def test_comic_due(self):
        min_s = settings.COMIC_MIN_MINUTES * 60
        max_s = settings.COMIC_MAX_MINUTES * 60
        # Too soon, even with plenty of lines
        self.assertFalse(comic_due(max(0.0, min_s - 60), have_lines=50, wanted_lines=8))
        # Past min gap but not enough lines yet (only when max > min; otherwise max fires)
        if max_s > min_s:
            self.assertFalse(comic_due(min_s, have_lines=7, wanted_lines=8))
        # Min gap + enough lines
        self.assertTrue(comic_due(min_s, have_lines=8, wanted_lines=8))
        # Max gap forces a comic even with no lines
        self.assertTrue(comic_due(max_s, have_lines=0, wanted_lines=8))

    def test_lines_wanted_clamped(self):
        self.assertEqual(lines_wanted({"lines_wanted_next": 12}), 12)
        self.assertEqual(lines_wanted({"lines_wanted_next": "99"}), 30)
        self.assertEqual(lines_wanted({"lines_wanted_next": 0}), 1)
        self.assertEqual(lines_wanted({}), 8)

    def test_parse_lines_wanted_from_state(self):
        self.assertEqual(parse_lines_wanted("3"), 3)
        self.assertEqual(parse_lines_wanted("99"), 30)
        self.assertEqual(parse_lines_wanted("nope", default=5), 5)

    def test_select_transcript_batch_is_oldest_first_and_capped(self):
        rows = [{"id": i, "scrambled_text": f"line {i}"} for i in range(10)]
        batch = select_transcript_batch(rows, 3)
        self.assertEqual([r["id"] for r in batch], [0, 1, 2])
        self.assertEqual(len(select_transcript_batch(rows, 99)), 10)


if __name__ == "__main__":
    unittest.main()
