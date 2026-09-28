import unittest
from server.main import comic_due
from server.story.narrative_engine import lines_wanted

MIN = 60


class TestPacing(unittest.TestCase):
    def test_comic_due(self):
        self.assertFalse(comic_due(4 * MIN, have_lines=50, wanted_lines=8))    # too soon, even with plenty
        self.assertFalse(comic_due(10 * MIN, have_lines=7, wanted_lines=8))    # not enough lines yet
        self.assertTrue(comic_due(5 * MIN, have_lines=8, wanted_lines=8))      # min gap + enough lines
        self.assertTrue(comic_due(24 * 60 * MIN, have_lines=0, wanted_lines=8))  # 1 day cap, even silent

    def test_lines_wanted_clamped(self):
        self.assertEqual(lines_wanted({"lines_wanted_next": 12}), 12)
        self.assertEqual(lines_wanted({"lines_wanted_next": "99"}), 30)
        self.assertEqual(lines_wanted({"lines_wanted_next": 0}), 1)
        self.assertEqual(lines_wanted({}), 8)


if __name__ == "__main__":
    unittest.main()
