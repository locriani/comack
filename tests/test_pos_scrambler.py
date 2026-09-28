import unittest
from server.privacy import pos_scrambler as ps
from server.privacy.pos_scrambler import scramble_text, _replacement


class TestPOSScrambler(unittest.TestCase):
    def test_replacement_matches_part_of_speech(self):
        self.assertIn(_replacement("radio", "NN"), ps.ABSURD_NOUNS)
        self.assertTrue(_replacement("radios", "NNS").endswith("s"))
        self.assertIn(_replacement("Marita", "NNP"), ps.ABSURD_PROPER_NOUNS)
        self.assertTrue(_replacement("learning", "VBG").endswith("ing"))
        self.assertTrue(_replacement("marinated", "VBN").endswith("ed"))
        self.assertIn(_replacement("quietly", "RB"), ps.ABSURD_ADVERBS)
        self.assertIn(_replacement("upon", "IN"), ps.PREPOSITIONS)
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

    def test_scramble_rate_zero(self):
        text = "Please turn off the lights."
        self.assertEqual(scramble_text(text, scramble_rate=0.0), text)


if __name__ == "__main__":
    unittest.main()
