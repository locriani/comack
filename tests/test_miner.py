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
             patch.object(thematic_miner.settings, "POS_SCRAMBLE_RATE", 0.0):
            rows = asyncio.run(thematic_miner.mine_sentences("Who moved it? I did."))
        self.assertEqual(rows, [
            "[VERY DEEPLY SUSPICIOUS • HIGH] The toaster knows.",
            "[CALM • MEDIUM] Pass the kazoo.",
        ])

    def test_llm_down_stores_nothing(self):
        with patch.object(narrative_engine, "_call_llama_cpp", AsyncMock(return_value=None)):
            self.assertEqual(asyncio.run(thematic_miner.mine_sentences("Hello there.")), [])


if __name__ == "__main__":
    unittest.main()
