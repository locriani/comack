import asyncio
import time
from unittest.mock import AsyncMock, patch
import httpx
from server import llm


def test_llm_calls_are_logged_with_their_raw_output():
    reply = httpx.Response(200, json={"choices": [{"message": {"content": '```json\n{"a": 1}\n```'}}]})
    with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=reply)):
        assert asyncio.run(llm.complete_json("sys", "hello", "http://x")) == {"a": 1}
    entry = llm.prompt_log[-1]
    assert (entry["system"], entry["prompt"]) == ("sys", "hello")
    assert entry["output"] == '```json\n{"a": 1}\n```'


def test_a_failed_call_is_logged_and_returns_none():
    with patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=httpx.ConnectError("down"))):
        assert asyncio.run(llm.complete_json("sys", "hello", "http://x")) is None
    assert llm.prompt_log[-1]["output"].startswith("error:")


def test_no_llm_url_means_no_call():
    with patch.object(llm.settings, "LLM_URL", ""), \
         patch.object(httpx.AsyncClient, "post", AsyncMock()) as post:
        assert asyncio.run(llm.complete_json("sys", "hello")) is None
    post.assert_not_called()
    assert llm.prompt_log[-1]["output"] == "error: LLM_URL is not set"


def test_entries_expire_after_15_minutes():
    llm.prompt_log.clear()
    now = time.time()
    llm.prompt_log.extend({"at": now - age} for age in (16 * 60, 15 * 60 + 1, 14 * 60, 0))
    llm.expire_prompt_log()
    assert [round(now - e["at"]) for e in llm.prompt_log] == [14 * 60, 0]
