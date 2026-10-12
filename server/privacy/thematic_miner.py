"""
Story element miner.
[audio] -> [transcribe] -> [25% swap + digit swap] -> HERE: [miner LLM pass 1]
  -> [25% swap + digit swap] -> [miner LLM pass 2] -> [LLM smoothing ("Comic lines")] -> stored.
The scrambled transcript itself is never stored; only lines that made it through every pass are.
"""
import re
import asyncio
import logging
from typing import List
from server import llm
from server.config import settings
from server.privacy.pos_scrambler import scramble_text

logger = logging.getLogger("comack.miner")

ENERGIES = ("low", "medium", "high")

SYSTEM_PROMPT = (
    "You turn overheard room speech into raw material for a comic strip. "
    "Some words were already swapped for random ones; treat them as real. "
    "Every line you write is a coherent, grammatical English sentence. "
    "Output ONLY valid JSON."
)

MINE_TASK = """For EACH sentence, in order, write one entry, or several if it says several distinct things:
- "vibe": its emotional vibe in at most 3 words
- "energy": "low", "medium", or "high"
- "content": a comic line riffing on the sentence (or that part of it), keeping its key words.
  It MUST be coherent, grammatical English that makes sense on its own."""

SMOOTH_TASK = """For EACH line, in order, write exactly one entry:
- "vibe" and "energy": the ones in its [VIBE • ENERGY] tag
- "content": the line rewritten as coherent, grammatical English that makes sense on its own.
  Keep its words (including the odd ones); change only what grammar needs."""


def split_sentences(text: str) -> List[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _content(item: dict) -> str:
    return str(item.get("content") or "").strip()


def format_row(item: dict) -> str:
    """`[VIBE • ENERGY] content`."""
    vibe = " ".join(str(item.get("vibe") or "deadpan").split()[:3]).upper()
    energy = str(item.get("energy", "")).lower()
    energy = energy if energy in ENERGIES else "medium"
    return f"[{vibe} • {energy.upper()}] {_content(item)}"


BATCH_SENTENCES = 12  # a 5-minute transcript would overflow the small miner model's context


async def mine_sentences(scrambled: str) -> List[str]:
    """Returns the stored rows (one or more per sentence) that survive every pass, BATCH_SENTENCES at a time."""
    sentences = split_sentences(scrambled)
    rows = []
    for i in range(0, len(sentences), BATCH_SENTENCES):
        rows += await _mine_batch(sentences[i:i + BATCH_SENTENCES])
    return rows


async def _mine_batch(sentences: List[str]) -> List[str]:
    """Mine, scramble, mine again, smooth. The passes are part of the privacy transform, so a line
    any pass fails to produce is dropped, never stored in a less-transformed state."""
    first = await _llm_pass("Overheard sentences:", MINE_TASK, sentences)
    # Second scramble (POS swap + digit swap) is CPU work: keep it off the event loop
    scrambled = await asyncio.to_thread(
        lambda: [scramble_text(_content(i), settings.POS_SCRAMBLE_RATE) for i in first])
    second = await _llm_pass("Overheard sentences:", MINE_TASK, scrambled)
    final = await _llm_pass("Comic lines:", SMOOTH_TASK, [format_row(i) for i in second])
    return [format_row(i) for i in final]


async def _llm_pass(header: str, task: str, inputs: List[str]) -> List[dict]:
    """One miner LLM call: {vibe, energy, content} dicts, in order (the task says how many per
    input). Entries the model leaves empty are dropped; a failed call returns []."""
    if not inputs:
        return []
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(inputs))
    prompt = f"""{header}
{numbered}

{task}
Output: {{"sentences": [{{"vibe": "...", "energy": "...", "content": "..."}}]}}"""
    result = await llm.miner_complete_json(SYSTEM_PROMPT, prompt)
    items = (result or {}).get("sentences") or []
    kept = [i for i in items if isinstance(i, dict) and _content(i)]
    if not kept or len(kept) < len(items):
        logger.warning(f"Miner pass kept {len(kept)} of {len(items)} entries for {len(inputs)} input(s); the rest are dropped.")
    return kept
