"""The one scheduler loop, per device (spec verification V7).

Two devices due at the same moment must both get comics: serially, through the
unchanged house-global generation_lock — the GPU is shared and issue numbers
are one sequence — each comic generated from its own device's room lines,
attributed to its own device, and queued in its own dispatcher slot.

DB isolation follows the test_storage.py convention: patch storage.DB_PATH to
a fresh file per test. The heavy render and LLM calls are patched at their
boundaries (comic_compositor, llm.complete_json), per the existing
test_compositor.py pattern.
"""
import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import server.storage as st
from server import llm as llm_client
from server import main as main_module
from server.storage import db

BIBLE = {"title": "Orbit Noir", "genre": "space noir", "premise": "A detective in orbit.",
         "tone": "rain-slick melancholy", "art_style": "high-contrast ink", "cast": ["Vex: the detective"]}
SCRIPT_REPLY = {
    "title": "The Rains",
    "layout_type": "two-columns",
    "lines_wanted_next": 4,
    "panels": [
        {"caption": "It never stopped raining.", "dialogue": []},
        {"caption": "It never would.", "dialogue": ["VEX: Not here."]},
    ],
}


class FakeDispatcher:
    def __init__(self):
        self.pending = []

    def queue_comic(self, comic):
        self.pending.append(comic)

    def force_dispatch(self):
        return self.pending.pop(0) if self.pending else None

    def get_status(self):
        return {"is_speaking": False}


class FakePipeline:
    def __init__(self, device_id):
        self.device_id = device_id
        self.connected = True
        self.dispatcher = FakeDispatcher()


class FakePipelines:
    def __init__(self, *device_ids):
        self.pipes = [FakePipeline(d) for d in device_ids]

    def for_device(self, device_id):
        return next(p for p in self.pipes if p.device_id == device_id)

    def active(self):
        return [p for p in self.pipes if p.connected]

    def all(self):
        return self.pipes


class SerialCompositor:
    """Stands in for the GPU-bound compositor and fails the test if two renders
    ever overlap — the observable effect of a broken generation_lock."""

    def __init__(self):
        self.inside = 0
        self.max_inside = 0

    def compose_comic(self, script):
        self.inside += 1
        self.max_inside = max(self.max_inside, self.inside)
        time.sleep(0.02)  # widen the race window: the other cycle must wait, not interleave
        self.inside -= 1
        return Path("png"), Path("bin")


@pytest.fixture
def isolated_db(monkeypatch):
    monkeypatch.setattr(st, "DB_PATH", Path(tempfile.mkdtemp()) / "scheduler.db")
    st.init_db()


def two_due_devices(monkeypatch) -> FakePipelines:
    """Two connected devices, each over its due-ness thresholds: more room
    lines than the default lines_wanted, and no comic since before the minimum
    gap (the scheduler starts counting from `started`, far in the past)."""
    fake = FakePipelines("living-room", "studio")
    monkeypatch.setattr(main_module, "pipelines", fake)
    for device in ("living-room", "studio"):
        for _ in range(10):
            db.save_transcript(time.time(), 1.0, "a scrambled line", device)
    return fake


def test_two_devices_due_at_once_get_serial_unique_attributed_comics(isolated_db, monkeypatch):
    """Spec V7: one tick, both devices due — two comics, one global issue
    sequence, each attributed to its own device and queued in its own slot."""
    fake = two_due_devices(monkeypatch)
    compositor = SerialCompositor()
    started = time.time() - 10 * 60  # past the 5-minute minimum for both devices
    with patch.object(llm_client, "complete_json", AsyncMock(side_effect=[BIBLE, SCRIPT_REPLY, BIBLE, SCRIPT_REPLY])), \
         patch.object(main_module, "comic_compositor", compositor):
        asyncio.run(main_module.scheduler_tick(started))

    comics = db.get_recent_comics()
    assert len(comics) == 2
    assert {c["issue_number"] for c in comics} == {1, 2}, "one global sequence, no collisions"
    assert {c["device_id"] for c in comics} == {"living-room", "studio"}
    assert {c["series_issue"] for c in comics} == {1, 1}, "each series numbers itself from #1"

    # Each device's own slot got its own issue — never the other device's.
    queued = {p.device_id: p.dispatcher.pending[0] for p in fake.all()}
    assert sorted(c["issue_number"] for c in queued.values()) == [1, 2]
    assert all(c["device_id"] == device for device, c in queued.items())

    # Each arc belongs to the right device, and each cycle consumed only its
    # own device's lines (the "wipes used lines" invariant from test_storage).
    arcs = {c["device_id"]: c["arc_id"] for c in comics}
    assert arcs["living-room"] != arcs["studio"]
    assert len(db.get_unprocessed_transcripts()) == 0

    # The lock held: the two renders never overlapped.
    assert compositor.max_inside == 1


def test_racing_generation_cycles_still_serialize_on_the_shared_lock(isolated_db, monkeypatch):
    """Two cycles entering at once (e.g. a scheduler tick and a secret-phrase
    trigger) both complete — serially, unique issues, correct attribution."""
    two_due_devices(monkeypatch)
    compositor = SerialCompositor()
    with patch.object(llm_client, "complete_json", AsyncMock(side_effect=[BIBLE, SCRIPT_REPLY, BIBLE, SCRIPT_REPLY])), \
         patch.object(main_module, "comic_compositor", compositor):
        async def race():
            await asyncio.gather(
                main_module.run_comic_generation_cycle("living-room"),
                main_module.run_comic_generation_cycle("studio"),
            )
        asyncio.run(race())

    assert compositor.max_inside == 1, "the shared lock must serialize entry points"
    comics = db.get_recent_comics()
    assert {c["issue_number"] for c in comics} == {1, 2}
    assert {c["device_id"] for c in comics} == {"living-room", "studio"}


def test_a_connected_room_can_be_due_while_another_is_not(isolated_db, monkeypatch):
    """Due-ness is per device: the room with lines gets a comic, the silent
    room's tick does not invent one."""
    fake = two_due_devices(monkeypatch)
    fake.pipes[1].connected = False  # studio stopped streaming: never due
    compositor = SerialCompositor()
    started = time.time() - 10 * 60
    with patch.object(llm_client, "complete_json", AsyncMock(side_effect=[BIBLE, SCRIPT_REPLY])), \
         patch.object(main_module, "comic_compositor", compositor):
        asyncio.run(main_module.scheduler_tick(started))

    comics = db.get_recent_comics()
    assert len(comics) == 1
    assert comics[0]["device_id"] == "living-room"
    assert fake.for_device("studio").dispatcher.pending == []
