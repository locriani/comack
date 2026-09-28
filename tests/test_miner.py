import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from server.privacy import thematic_miner
from server.story.narrative_engine import narrative_engine


class TestMiner(unittest.TestCase):
    def test_one_row_per_sentence_with_capped_vibe_and_energy(self):
        llm = {"sentences": [
            {"vibe": "very deeply suspicious badger", "energy": "HIGH", "content": "The toaster knows."},
            {"vibe": "calm", "energy": "extreme", "content": "Pass the kazoo."},
            {"vibe": "extra", "energy": "low", "content": "Beyond the input count."},
        ]}
        with patch.object(narrative_engine, "_call_llama_cpp", AsyncMock(return_value=llm)), \
             patch.object(thematic_miner, "scramble_text", lambda t, rate: f"~{t}~"):
            rows = asyncio.run(thematic_miner.mine_sentences("Who moved it? I did."))
        self.assertEqual(rows, [   # smoothing got no "lines": the second pass's lines are kept
            "[VERY DEEPLY SUSPICIOUS • HIGH] The toaster knows.",
            "[CALM • MEDIUM] Pass the kazoo.",
        ])

    def test_mine_scramble_mine_smooth(self):
        llm = AsyncMock(side_effect=[
            {"sentences": [{"vibe": "a", "energy": "low", "content": "one"}]},    # miner pass 1
            {"sentences": [{"vibe": "b", "energy": "high", "content": "two"}]},   # miner pass 2
            {"lines": ["three"]},                                                 # smoothing
        ])
        with patch.object(narrative_engine, "_call_llama_cpp", llm), \
             patch.object(thematic_miner, "scramble_text", lambda t, rate: f"~{t}~"):
            rows = asyncio.run(thematic_miner.mine_sentences("Pass it."))
        pass2, smooth = (c.args[1] for c in llm.await_args_list[1:])
        self.assertIn("~one~", pass2)                                   # pass 2 mines the scrambled pass 1
        self.assertIn("two", smooth)
        self.assertNotIn("~two~", smooth)                               # no scramble before smoothing
        self.assertEqual(rows, ["[B • HIGH] three"])

    def test_second_pass_down_keeps_the_scrambled_first_pass(self):
        llm = AsyncMock(side_effect=[{"sentences": [{"vibe": "a", "energy": "low", "content": "one"}]}, None, None])
        with patch.object(narrative_engine, "_call_llama_cpp", llm), \
             patch.object(thematic_miner, "scramble_text", lambda t, rate: f"~{t}~"):
            self.assertEqual(asyncio.run(thematic_miner.mine_sentences("Pass it.")), ["[A • LOW] ~one~"])

    def test_long_transcripts_are_mined_in_batches(self):
        llm = AsyncMock(return_value={"sentences": [{"vibe": "x", "energy": "low", "content": "y"}] * 12})
        with patch.object(narrative_engine, "_call_llama_cpp", llm):
            rows = asyncio.run(thematic_miner.mine_sentences(" ".join(["Hi."] * 30)))
        self.assertEqual(llm.await_count, 9)   # 3 batches x (mine + mine + smooth)
        self.assertEqual(len(rows), 30)

    def test_llm_down_stores_nothing(self):
        with patch.object(narrative_engine, "_call_llama_cpp", AsyncMock(return_value=None)):
            self.assertEqual(asyncio.run(thematic_miner.mine_sentences("Hello there.")), [])


if __name__ == "__main__":
    unittest.main()
