"""Simulator device identity (spec D6): flags, request lines, refresh-tag filter.

The desktop simulator is the verification vehicle for the whole multi-device
feature, so its wire behavior is pinned here. The simulator is a script, not
a package — the module is loaded from its file path. Covered:

- request lines carry identity: /ws/audio and the current.png poll gain
  ?device_id=&device_token= exactly when identity is presented, including
  after the poll's existing cache-buster query;
- legacy mode (no flags) is byte-identical to today: no device params on any
  request line, checked both on the URL builders and through the actual
  reconnect-loop task;
- a partial pair is presented as-is (never completed or downgraded) —
  rejection is the server's job (4401 on the WebSocket, 401 on HTTP);
- refresh-tag filtering: a SNEAKY_COMIC_REFRESH tagged for another device is
  ignored; own-tag, tokenless-'default', and untagged (a pre-device server)
  events are acted on.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_SIM_PATH = Path(__file__).resolve().parents[1] / "simulator" / "reterminal_sim.py"
_spec = importlib.util.spec_from_file_location("reterminal_sim", _SIM_PATH)
sim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim)

WS_BASE = "ws://comack:8000"
HTTP_BASE = "http://comack:8000"
REFRESH = {"event": "SNEAKY_COMIC_REFRESH", "issue_number": 7, "silence_seconds": 31.0}


# --- Request lines carry identity ---


def test_audio_request_line_carries_identity_pair():
    assert sim.with_device_identity(f"{WS_BASE}/ws/audio", "living-room", "tok-abc") == \
        f"{WS_BASE}/ws/audio?device_id=living-room&device_token=tok-abc"


def test_png_request_line_appends_identity_after_cache_buster():
    url = sim.current_png_url(HTTP_BASE, "living-room", "tok-abc", cache_buster=1790000000.0)
    assert url == f"{HTTP_BASE}/api/comics/current.png?t=1790000000.0&device_id=living-room&device_token=tok-abc"


def test_identity_values_are_urlencoded():
    assert sim.with_device_identity(f"{WS_BASE}/ws/audio", "living room", "tok/abc") == \
        f"{WS_BASE}/ws/audio?device_id=living+room&device_token=tok%2Fabc"


def test_partial_identity_presented_as_is_id_only():
    assert sim.with_device_identity(f"{WS_BASE}/ws/audio", "living-room", None) == \
        f"{WS_BASE}/ws/audio?device_id=living-room"


def test_partial_identity_presented_as_is_token_only():
    assert sim.with_device_identity(f"{WS_BASE}/ws/audio", None, "tok-abc") == \
        f"{WS_BASE}/ws/audio?device_token=tok-abc"


# --- Legacy mode is byte-identical to today ---


def test_legacy_audio_request_line_unchanged():
    assert sim.with_device_identity(f"{WS_BASE}/ws/audio", None, None) == f"{WS_BASE}/ws/audio"


def test_legacy_png_request_line_unchanged():
    assert sim.current_png_url(HTTP_BASE, None, None, cache_buster=1790000000.0) == \
        f"{HTTP_BASE}/api/comics/current.png?t=1790000000.0"


def _capture_first_connect(monkeypatch):
    """Record every URL websockets.connect is called with; the first attempt
    raises so the task lands in its reconnect sleep (bounded below)."""
    captured = []

    def fake_connect(url):
        captured.append(url)
        raise ConnectionError("one attempt is enough")

    monkeypatch.setattr(sim, "websockets", SimpleNamespace(connect=fake_connect))
    return captured


def _run_bounded(coro):
    """Run a reconnect-loop task just long enough for its first attempt."""
    async def run():
        task = asyncio.create_task(coro)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(task, timeout=0.5)

    asyncio.run(run())


def test_audio_task_sends_the_identity_request_line(monkeypatch):
    captured = _capture_first_connect(monkeypatch)
    _run_bounded(sim.audio_streamer_task(WS_BASE, device_id="living-room", device_token="tok-abc"))
    assert captured == [f"{WS_BASE}/ws/audio?device_id=living-room&device_token=tok-abc"]


def test_legacy_audio_task_sends_the_tokenless_request_line(monkeypatch):
    captured = _capture_first_connect(monkeypatch)
    _run_bounded(sim.audio_streamer_task(WS_BASE))
    assert captured == [f"{WS_BASE}/ws/audio"]


# --- Refresh-tag filtering: a wall flashes only for its own room ---


def test_own_tag_matches():
    assert sim.is_own_refresh(REFRESH | {"device_id": "living-room"}, "living-room") is True


def test_foreign_tag_does_not_match():
    assert sim.is_own_refresh(REFRESH | {"device_id": "kitchen"}, "living-room") is False


def test_tokenless_matches_default_tag():
    assert sim.is_own_refresh(REFRESH | {"device_id": "default"}, None) is True


def test_untagged_event_matches_any_device():
    assert sim.is_own_refresh(REFRESH, "living-room") is True
    assert sim.is_own_refresh(REFRESH, None) is True


def _listen_for_messages(monkeypatch, messages, device_id):
    """Drive the event listener through the given server messages (JSON
    strings), then bound it while it idles; return what it acted on."""
    refreshes = []

    class FakeWS:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False  # never suppress — cancellation must end the bounded run

        async def recv(self):
            if messages:
                return messages.pop(0)
            await asyncio.sleep(3600)  # stay connected; the run is bounded

    monkeypatch.setattr(sim, "websockets", SimpleNamespace(connect=lambda url: FakeWS()))

    async def run():
        task = asyncio.create_task(
            sim.event_listener_task(WS_BASE, on_refresh=refreshes.append, device_id=device_id))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(task, timeout=0.5)

    asyncio.run(run())
    return refreshes


def test_listener_ignores_other_devices_reveals(monkeypatch):
    messages = [
        json.dumps(REFRESH | {"device_id": "kitchen"}),
        json.dumps(REFRESH | {"device_id": "living-room", "issue_number": 8}),
    ]
    refreshes = _listen_for_messages(monkeypatch, messages, "living-room")
    assert [r["issue_number"] for r in refreshes] == [8]


def test_listener_tokenless_acts_on_default_tag_only(monkeypatch):
    messages = [
        json.dumps(REFRESH | {"device_id": "default", "issue_number": 4}),
        json.dumps(REFRESH | {"device_id": "kitchen", "issue_number": 9}),
    ]
    refreshes = _listen_for_messages(monkeypatch, messages, None)
    assert [r["issue_number"] for r in refreshes] == [4]


def test_listener_acts_on_untagged_event_from_pre_device_server(monkeypatch):
    refreshes = _listen_for_messages(monkeypatch, [json.dumps(REFRESH)], "living-room")
    assert [r["issue_number"] for r in refreshes] == [7]


def test_listener_tokenless_acts_on_untagged_event(monkeypatch):
    refreshes = _listen_for_messages(monkeypatch, [json.dumps(REFRESH)], None)
    assert [r["issue_number"] for r in refreshes] == [7]

