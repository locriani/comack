"""
Story element miner.
[audio] -> [transcribe] -> [10% swap + digit swap] -> HERE: [LLM content per sentence] -> stored.
The scrambled transcript itself is never stored; only these generated lines are.
"""
import re
import logging
from typing import List
from server.config import settings

logger = logging.getLogger("comack.miner")

ENERGIES = ("low", "medium", "high")

SYSTEM_PROMPT = (
    "You turn overheard room speech into raw material for a surreal comic strip. "
    "Some words were already swapped for absurd ones; treat them as real. "
    "Every line you write is a coherent, grammatical English sentence. "
    "Output ONLY valid JSON."
)


def split_sentences(text: str) -> List[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def format_row(item: dict) -> str:
    """`[VIBE • ENERGY] content`"""
    vibe = " ".join(str(item.get("vibe") or "deadpan").split()[:3]).upper()
    energy = str(item.get("energy", "")).lower()
    energy = energy if energy in ENERGIES else "medium"
    return f"[{vibe} • {energy.upper()}] {item.get('content') or ''}"


BATCH_SENTENCES = 12  # a 5-minute transcript would overflow the small miner model's context


async def mine_sentences(scrambled: str) -> List[str]:
    """Returns one stored row per detected sentence, BATCH_SENTENCES per LLM call."""
    sentences = split_sentences(scrambled)
    rows = []
    for i in range(0, len(sentences), BATCH_SENTENCES):
        rows += await _mine_batch(sentences[i:i + BATCH_SENTENCES])
    return rows


async def _mine_batch(sentences: List[str]) -> List[str]:
    from server.story.narrative_engine import narrative_engine
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))
    prompt = f"""Overheard sentences:
{numbered}

For EACH sentence, in order, write:
- "vibe": its emotional vibe in at most 3 words
- "energy": "low", "medium", or "high"
- "content": a surreal comic line riffing on the sentence, keeping its key words.
  It MUST be coherent, grammatical English that makes sense on its own.
Output: {{"sentences": [{{"vibe": "...", "energy": "...", "content": "..."}}]}} with exactly {len(sentences)} entries."""
    result = await narrative_engine._call_llama_cpp(SYSTEM_PROMPT, prompt, settings.MINER_LLAMA_URL)
    items = (result or {}).get("sentences") or []
    if not items:
        # ponytail: LLM down = these sentences are dropped; add a local fallback if outages are common.
        logger.warning(f"Miner got no LLM content; dropped {len(sentences)} sentence(s).")
    return [format_row(i) for i in items[:len(sentences)] if isinstance(i, dict)]
