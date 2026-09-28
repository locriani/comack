import asyncio
import time
from unittest.mock import AsyncMock, patch
import httpx
from server.story import narrative_engine as ne


def test_llm_calls_are_logged_with_their_raw_output():
    reply = httpx.Response(200, json={"choices": [{"message": {"content": '```json\n{"a": 1}\n```'}}]})
    with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=reply)):
        assert asyncio.run(ne.narrative_engine._call_llama_cpp("sys", "hello", "http://x")) == {"a": 1}
    entry = ne.prompt_log[-1]
    assert (entry["system"], entry["prompt"]) == ("sys", "hello")
    assert entry["output"] == '```json\n{"a": 1}\n```'


def test_entries_expire_after_15_minutes():
    ne.prompt_log.clear()
    now = time.time()
    ne.prompt_log.extend({"at": now - age} for age in (16 * 60, 15 * 60 + 1, 14 * 60, 0))
    ne.expire_prompt_log()
    assert [round(now - e["at"]) for e in ne.prompt_log] == [14 * 60, 0]
