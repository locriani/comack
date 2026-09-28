import unittest
from server.privacy.pos_scrambler import scramble_text, _classify_word

class TestPOSScrambler(unittest.TestCase):
    def test_classify_word(self):
        cat, hint = _classify_word("running")
        self.assertEqual(cat, "VERB")
        self.assertEqual(hint, "ing")
        
        cat, hint = _classify_word("investigated")
        self.assertEqual(cat, "VERB")
        self.assertEqual(hint, "ed")
        
        cat, _ = _classify_word("beautiful")
        self.assertEqual(cat, "ADJ")
        
        cat, _ = _classify_word("organization")
        self.assertEqual(cat, "NOUN")
        
        cat, _ = _classify_word("the")
        self.assertEqual(cat, "OTHER")

    def test_scramble_preserves_length_and_structure(self):
        text = "The doctor investigated the suspicious machine carefully."
        scrambled = scramble_text(text, scramble_rate=1.0)
        
        self.assertTrue(scrambled.startswith("The "))
        self.assertNotEqual(scrambled, text)
        self.assertTrue(scrambled.endswith("."))

    def test_scramble_rate_zero(self):
        text = "Please turn off the lights."
        self.assertEqual(scramble_text(text, scramble_rate=0.0), text)

if __name__ == "__main__":
    unittest.main()
