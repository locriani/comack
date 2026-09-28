import unittest
from server.privacy import pos_scrambler as ps
from server.privacy.pos_scrambler import scramble_text, _replacement


class TestPOSScrambler(unittest.TestCase):
    def test_replacement_matches_part_of_speech(self):
        pools = ps.pools()
        for tag in ["NN", "NNS", "NNP", "VB", "VBD", "VBG", "VBN", "VBZ", "JJ", "RB"]:
            self.assertGreater(len(pools[tag]), 400, tag)   # wide variety, not a short list
        self.assertIn(_replacement("radio", "NN"), pools["NN"])
        self.assertIn(_replacement("radios", "NNS"), pools["NNS"])
        self.assertIn(_replacement("Marita", "NNP"), pools["NNP"])
        self.assertIn(_replacement("learning", "VBG"), pools["VBG"])
        self.assertIn(_replacement("marinated", "VBN"), pools["VBN"])
        self.assertIn(_replacement("want", "VBP"), pools["VB"])
        self.assertTrue(_replacement("quietly", "RB").endswith("ly"))
        self.assertIn(_replacement("upon", "IN"), ps.PREPOSITIONS)
        self.assertFalse(ps.BLOCKLIST & {w.lower() for p in pools.values() for w in p})
        # Grammar words stay put
        self.assertIsNone(_replacement("that", "IN"))    # conjunction, not a preposition
        self.assertIsNone(_replacement("was", "VBD"))    # auxiliary
        self.assertIsNone(_replacement("Can", "NNP"))    # tagger slip on a sentence-initial modal
        self.assertIsNone(_replacement("very", "RB"))    # not an -ly adverb
        self.assertIsNone(_replacement("don't", "VBP"))

    def test_rate_one_swaps_every_eligible_word_and_keeps_structure(self):
        text = "Upon learning that Marita wants to use it for her radio, the gravity marinated."
        out = scramble_text(text, scramble_rate=1.0)
        self.assertNotEqual(out, text)
        self.assertEqual(out.count(","), 1)
        self.assertTrue(out.endswith("."))
        self.assertIn(" that ", out)
        self.assertIn(" to ", out)

    def test_rate_ten_percent_of_words(self):
        text = " ".join(["The lawyer carefully reviewed the contract."] * 20)  # 120 words
        changed = [sum(a != b for a, b in zip(text.split(), scramble_text(text, 0.10).split())) for _ in range(20)]
        self.assertTrue(all(11 <= c <= 13 for c in changed), changed)

    def test_numbers_of_three_plus_digits_get_every_digit_swapped(self):
        text = "Call 555-867-5309 at 1,234 Elm, 3.14 or 42 at 10:30."
        for _ in range(20):
            out = scramble_text(text, scramble_rate=0.0)   # digits swap even at rate 0
            self.assertEqual(len(out), len(text))
            nums = [(a, b) for a, b in zip(text[:text.index(" or")], out) if a.isdigit()]
            self.assertTrue(all(b.isdigit() and a != b for a, b in nums), out)
            self.assertTrue(out.endswith(" or 42 at 10:30."), out)   # short numbers stay

    def test_scramble_rate_zero(self):
        text = "Please turn off the lights."
        self.assertEqual(scramble_text(text, scramble_rate=0.0), text)


if __name__ == "__main__":
    unittest.main()
