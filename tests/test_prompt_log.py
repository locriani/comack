import asyncio
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
