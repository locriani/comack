"""
The llama.cpp client (OpenAI-compatible /v1 endpoint on comack-server) and the in-memory log of
every model call that the dashboard's debug view shows.
"""
import json
import time
import logging
from collections import deque
from typing import Dict, Any, Optional
import httpx
from server.config import settings

logger = logging.getLogger("comack.llm")

# Recent model prompts and outputs for the dashboard's debug view. The miner's prompts hold
# the first-pass scrambled transcript, which may not be stored: this lives in memory only.
prompt_log: deque = deque(maxlen=60)
PROMPT_LOG_TTL_S = 15 * 60


def expire_prompt_log():
    """Forget calls made more than PROMPT_LOG_TTL_S ago (entries are in call order)."""
    cutoff = time.time() - PROMPT_LOG_TTL_S
    while prompt_log and prompt_log[0]["at"] < cutoff:
        prompt_log.popleft()


def log_call(model: str, system: str, prompt: str) -> Dict[str, Any]:
    """Log a model call; the caller fills in the entry's "output" when it has one."""
    entry = {"at": time.time(), "model": model, "system": system, "prompt": prompt, "output": None}
    prompt_log.append(entry)
    return entry


async def complete_json(system_prompt: str, user_prompt: str, base_url: str = "") -> Optional[Dict[str, Any]]:
    """One chat completion asking for JSON (default: the main model). None when the call fails
    or the reply isn't JSON; callers treat that as "no output", never as a reason to fall back."""
    base = (base_url or settings.LLAMA_CPP_URL).rstrip('/')
    url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
    payload = {
        "model": settings.LLAMA_CPP_MODEL or "default",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.85,
        "response_format": {"type": "json_object"}
    }
    entry = log_call(url, system_prompt, user_prompt)
    try:
        # Reasoning model with thinking on: generation can take minutes.
        async with httpx.AsyncClient(timeout=600.0) as client:
            logger.info(f"Sending prompt to llama.cpp at {url}")
            resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            entry["output"] = f"HTTP {resp.status_code}: {resp.text}"
            logger.warning(f"llama.cpp returned status {resp.status_code}: {resp.text[:200]}")
            return None
        content = entry["output"] = resp.json()["choices"][0]["message"]["content"]
        # Extract JSON block if wrapped in markdown
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        return json.loads(content)
    except Exception as e:
        entry["output"] = entry["output"] or f"error: {e!r}"
        logger.warning(f"Failed to query llama.cpp at {url}: {e}")
        return None
