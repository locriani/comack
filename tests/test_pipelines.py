"""Per-device audio pipelines (spec verification V1 + per-device dispatcher slots).

V1: two devices' audio interleaves through two pipelines and comes out
attributed — segments carry the pipeline's device, transcripts land on the
right device's rows, each noise floor adapts to its own room, and one room's
silence clock never hears the other room's speech. The old single-VAD code
could not pass this by construction: there was one buffer, one floor, one
clock, so interleaved streams merged no matter what.

DB isolation follows the test_storage.py convention: patch storage.DB_PATH to
a fresh file per test.
"""
import asyncio
import struct
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import server.storage as st
from server import main as main_module
from server.audio.vad import AudioVAD
from server.pipelines import PipelineRegistry
from server.storage import db


def pcm(level: int, ms: int = 100) -> bytes:
    """ms of 16kHz 16-bit mono PCM at a constant amplitude (the RMS is that level)."""
    samples = int(16000 * ms / 1000)
    return struct.pack(f"<{samples}h", *([level] * samples))


LOUD = pcm(8000)   # clear speech above every threshold in play
NOISY = pcm(400)   # a humming room: above the initial 200 floor, below the 650 gate
QUIET = pcm(5)     # a silent room


@pytest.fixture
def isolated_db(monkeypatch):
    monkeypatch.setattr(st, "DB_PATH", Path(tempfile.mkdtemp()) / "pipelines.db")
    st.init_db()


def test_vad_stamps_its_device_on_emitted_segments():
    segments = []
    vad = AudioVAD(on_speech_segment=segments.append, device_id="living-room")
    for _ in range(15):
        vad.process_chunk(LOUD)
    for _ in range(10):
        vad.process_chunk(QUIET)
    assert segments, "speech followed by the pad of silence must emit a segment"
    assert [s.device_id for s in segments] == ["living-room"]


def test_bare_vad_segments_stay_unattributed():
    """A VAD constructed without a device emits device_id=None (no silent
    misattribution — downstream decides what an unattributed segment means)."""
    segments = []
    vad = AudioVAD(on_speech_segment=segments.append)
    for _ in range(15):
        vad.process_chunk(LOUD)
    for _ in range(10):
        vad.process_chunk(QUIET)
    assert segments
    assert [s.device_id for s in segments] == [None]


def test_interleaved_streams_reach_the_right_batches_and_transcript_rows(isolated_db):
    """Spec V1: chunk-by-chunk interleaving of two rooms produces segments
    attributed to the right source, and each source's rows hold only its own
    speech."""
    registry = PipelineRegistry()
    a = registry.for_device("living-room")
    b = registry.for_device("studio")

    for _ in range(15):  # both rooms speak simultaneously
        a.vad.process_chunk(LOUD)
        b.vad.process_chunk(LOUD)
    for _ in range(10):  # both rooms go quiet
        a.vad.process_chunk(QUIET)
        b.vad.process_chunk(QUIET)

    batch_a, batch_b = a.transcriber.take(), b.transcriber.take()
    assert batch_a and batch_b, "each room's speech must survive in its own batch"

    # Each batch flows through the real transcription+mining path and lands on
    # its own device's rows — and only there. The miner itself is stubbed: it
    # keeps zero rows with LLM_URL unset, and its content logic is not this
    # test's subject — the attribution plumbing is.
    result_a = a.transcriber.transcribe_batch(*batch_a)
    result_b = b.transcriber.transcribe_batch(*batch_b)
    assert result_a and result_b
    with patch.object(main_module, "mine_sentences",
                      AsyncMock(side_effect=[["a line from the living room"], ["a line from the studio"]])):
        asyncio.run(main_module.mine_and_store(*result_a, device_id="living-room"))
        asyncio.run(main_module.mine_and_store(*result_b, device_id="studio"))

    rows_a = db.get_unprocessed_transcripts("living-room")
    rows_b = db.get_unprocessed_transcripts("studio")
    assert [r["device_id"] for r in rows_a] == ["living-room"]
    assert [r["device_id"] for r in rows_b] == ["studio"]
    assert len(db.get_unprocessed_transcripts()) == len(rows_a) + len(rows_b)


def test_noise_floors_and_silence_clocks_are_per_room(isolated_db):
    """Each noise floor adapts to its own room; one room's speech never bleeds
    into the other room's threshold or clock."""
    registry = PipelineRegistry()
    a = registry.for_device("living-room")
    b = registry.for_device("studio")

    for _ in range(20):  # room B hums along; room A is silent
        b.vad.process_chunk(NOISY)
        a.vad.process_chunk(QUIET)
    assert not b.vad.is_speaking
    assert b.vad._ambient_noise > 300.0, "B's floor must learn its room's hum"
    assert a.vad._ambient_noise < 100.0, "A's floor must learn its room's quiet"
    # The two floors moved in opposite directions over the same ticks — the
    # observable effect of one floor per room instead of one shared floor.

    for _ in range(6):  # someone speaks in room A only
        a.vad.process_chunk(LOUD)
        b.vad.process_chunk(NOISY)
    assert a.vad.is_speaking, "A heard speech"
    assert not b.vad.is_speaking, "B heard only its own hum (its floor rose to meet it)"
    assert b.vad.current_silence_seconds > 0, "B's clock kept running through A's speech"


def test_a_device_never_holds_another_devices_comic(isolated_db):
    """Dispatcher slots are per device: restart recovery queues a held comic
    into its owning device's pipeline, and the other device can neither see it
    nor release it."""
    arc_a = db.create_arc("Orbit Noir", "space noir", "A detective in orbit.", 10, "living-room")
    db.save_comic(issue_number=1, arc_id=arc_a, title="The Rains", layout_type="two-columns",
                  panel_count=2, script_json={}, image_path="x.png", bin_path="x.bin")

    async def build():
        registry = PipelineRegistry()
        a = registry.for_device("living-room")
        b = registry.for_device("studio")
        assert a.dispatcher.pending_comic is not None
        assert a.dispatcher.pending_comic["issue_number"] == 1
        assert b.dispatcher.pending_comic is None, "studio's slot never sees living-room's hold"
        a.dispatcher.force_dispatch()
        assert b.dispatcher.force_dispatch() is None, "and it cannot release it either"

    asyncio.run(build())
