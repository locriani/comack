import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from server.privacy import thematic_miner
from server import llm as llm_client


def echo(system, prompt, url="", model=""):
    """A model that answers one entry per numbered input line."""
    n = sum(1 for line in prompt.splitlines() if line.split(". ", 1)[0].isdigit())
    return {"sentences": [{"vibe": "x", "energy": "low", "content": "y"}] * n}


class TestMiner(unittest.TestCase):
    def test_rows_with_capped_vibe_and_energy_and_a_sentence_can_yield_several(self):
        llm = {"sentences": [
            {"vibe": "very deeply suspicious badger", "energy": "HIGH", "content": "The toaster knows."},
            {"vibe": "calm", "energy": "extreme", "content": "Pass the kazoo."},
            {"vibe": "extra", "energy": "low", "content": "A third thing it said."},
        ]}
        with patch.object(llm_client, "complete_json", AsyncMock(return_value=llm)), \
             patch.object(thematic_miner, "scramble_text", lambda t, rate: f"~{t}~"):
            rows = asyncio.run(thematic_miner.mine_sentences("Who moved it? I did."))
        self.assertEqual(rows, [
            "[VERY DEEPLY SUSPICIOUS • HIGH] The toaster knows.",
            "[CALM • MEDIUM] Pass the kazoo.",
            "[EXTRA • LOW] A third thing it said.",
        ])

    def test_one_sentence_saying_two_things_keeps_both(self):
        two = {"sentences": [{"vibe": "a", "energy": "low", "content": "one"},
                             {"vibe": "b", "energy": "high", "content": "two"}]}
        llm = AsyncMock(return_value=two)
        with patch.object(llm_client, "complete_json", llm), \
             patch.object(thematic_miner, "scramble_text", lambda t, rate: t):
            rows = asyncio.run(thematic_miner.mine_sentences("Pass it and lock the door."))
        self.assertEqual(rows, ["[A • LOW] one", "[B • HIGH] two"])
        pass1, pass2, smooth = (c.args[1] for c in llm.await_args_list)
        self.assertNotIn("exactly", pass1)             # the miner may split a sentence
        self.assertIn("2. two", pass2)                 # both lines go through every pass
        self.assertIn("exactly one entry", smooth)     # smoothing is one-for-one

    def test_mine_scramble_mine_smooth(self):
        llm = AsyncMock(side_effect=[
            {"sentences": [{"vibe": "a", "energy": "low", "content": "one"}]},     # miner pass 1
            {"sentences": [{"vibe": "b", "energy": "high", "content": "two"}]},    # miner pass 2
            {"sentences": [{"vibe": "c", "energy": "low", "content": "three"}]},   # smoothing
        ])
        with patch.object(llm_client, "complete_json", llm), \
             patch.object(thematic_miner, "scramble_text", lambda t, rate: f"~{t}~"):
            rows = asyncio.run(thematic_miner.mine_sentences("Pass it."))
        pass2, smooth = (c.args[1] for c in llm.await_args_list[1:])
        self.assertIn("~one~", pass2)                  # pass 2 mines the scrambled pass 1
        self.assertIn("[B • HIGH] two", smooth)        # smoothing gets pass 2's tags, no scramble
        self.assertEqual(rows, ["[C • LOW] three"])    # the stored tags are smoothing's

    def test_a_line_any_pass_fails_is_dropped(self):
        botched = {"sentences": [{"vibe": "a", "energy": "low", "content": "one"}, {"vibe": "a"}]}
        for failing in range(3):
            for bad, left in ((None, 0), (botched, 1)):
                calls = []

                async def llm(system, prompt, url="", model=""):
                    calls.append(prompt)
                    return bad if len(calls) - 1 == failing else echo(system, prompt)

                with self.subTest(failing=failing, bad=bad), \
                     patch.object(llm_client, "complete_json", llm), \
                     patch.object(thematic_miner, "scramble_text", lambda t, rate: t):
                    self.assertEqual(len(asyncio.run(thematic_miner.mine_sentences("A. B."))), left)

    def test_long_transcripts_are_mined_in_batches(self):
        llm = AsyncMock(side_effect=echo)
        with patch.object(llm_client, "complete_json", llm):
            rows = asyncio.run(thematic_miner.mine_sentences(" ".join(["Hi."] * 30)))
        self.assertEqual(llm.await_count, 9)   # 3 batches x (mine + mine + smooth)
        self.assertEqual(len(rows), 30)

    def test_llm_down_stores_nothing(self):
        with patch.object(llm_client, "complete_json", AsyncMock(return_value=None)):
            self.assertEqual(asyncio.run(thematic_miner.mine_sentences("Hello there.")), [])


if __name__ == "__main__":
    unittest.main()
