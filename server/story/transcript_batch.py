"""Room-note batching for one comic generation cycle (pure policy, no I/O)."""
from typing import Any, List, Sequence

from server.config import settings

LINES_WANTED_MIN = 1
LINES_WANTED_MAX = 30


def clamp_lines_wanted(value: int) -> int:
    """Server-enforced pacing bounds (matches narrative_engine / LLM schema)."""
    return max(LINES_WANTED_MIN, min(LINES_WANTED_MAX, value))


def parse_lines_wanted(raw: str | None, default: int | None = None) -> int:
    """Device pacing key from persisted state (or DEFAULT_LINES_WANTED)."""
    fallback = default if default is not None else settings.DEFAULT_LINES_WANTED
    try:
        return clamp_lines_wanted(int(raw if raw is not None else fallback))
    except (TypeError, ValueError):
        return clamp_lines_wanted(fallback)


def select_transcript_batch(unprocessed: Sequence[dict[str, Any]], wanted: int) -> List[dict[str, Any]]:
    """Oldest-first notes for this issue; the rest stay queued for a later cycle."""
    return list(unprocessed[: clamp_lines_wanted(wanted)])
