"""Two-device acceptance pass: the spec's verification matrix (V1–V7) as one suite.

The live equivalent — two desktop simulators streaming against a running
server — validated this branch before merge; this suite pins the same
semantics for CI, on the light core (no torch / faster-whisper):

- the faster-whisper model is absent, so Transcriber falls back to its
  conversational mock text (the plumbing under test, not the acoustic model);
- `llm.complete_json` is faked, so arc bibles, scripts, and miner passes are
  deterministic;
- the compositor draws its procedural art (flux_generator returns None
  without torch) — same output contract, no GPU.

Each test runs the real FastAPI app and the real per-device pipelines, so a
regression in scoping (a global query, an untagged event, a merged segment)
fails here exactly as it would in a two-room deployment.
"""
import asyncio
import importlib.util
import json
import math
import struct
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import server.storage as st
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server import main as main_module
from server.pipelines import pipelines
from server.story import narrative_engine

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """A fresh database per test, at the storage-module seam every other
    test suite uses (db methods read storage.DB_PATH at call time)."""
    monkeypatch.setattr(st, "DB_PATH", Path(tempfile.mkdtemp()) / "acceptance.db")
    st.init_db()
    yield


@pytest.fixture(autouse=True)
def scratch_comics_dir(monkeypatch):
    """compose_comic writes issue_0001.png/bin for an issue numbered 1 —
    exactly the committed fixture files in data/comics. The comic output
    directory is imported by value into BOTH consumers (the compositor that
    writes it, main.py's issue readers), so both bindings are redirected to
    the same scratch directory and generation never touches the repo tree."""
    import server.render.comic_compositor as compositor

    comics_dir = Path(tempfile.mkdtemp()) / "comics"
    comics_dir.mkdir()  # config.py mkdirs the real one at import; the scratch needs it too
    monkeypatch.setattr(compositor, "COMICS_DIR", comics_dir)
    monkeypatch.setattr(main_module, "COMICS_DIR", comics_dir)
    yield


@pytest.fixture(autouse=True)
def fresh_pipelines_and_engines(monkeypatch):
    """PipelineRegistry, the engine cache, and generation_lock all outlive a
    test: the registry and engines key by device id (a later test would
    inherit an earlier test's dispatcher slot against a different temp DB),
    and the module-level lock can arrive [locked] and loop-bound from a
    lifespan test whose boot comic cycle was killed mid-hold — the same
    stand-in swap test_delivery.py uses."""
    monkeypatch.setattr(main_module, "generation_lock", asyncio.Lock())
    pipelines._pipes.clear()
    narrative_engine._engines.clear()
    yield
    pipelines._pipes.clear()
    narrative_engine._engines.clear()


@pytest.fixture(autouse=True)
def hold_the_silence_gate(monkeypatch):
    """Comics stay in stealth for the whole test (gate = 1h): reveals are
    explicit force_dispatch calls, never the 1s monitor race of a 0s gate."""
    monkeypatch.setattr(main_module.settings, "SNEAKY_SILENCE_SECONDS", 3600.0)


@pytest.fixture
def client():
    return TestClient(main_module.app)


@pytest.fixture
def devices(client):
    """Two registered devices with their one-shown tokens."""
    created = []
    for name in ("Living Room", "Studio"):
        body = client.post("/api/devices", json={"name": name}).json()
        created.append((body["id"], body.pop("token")))
    return created  # [("living-room", token), ("studio", token)]


def speech_chunk(seconds: float = 0.1) -> bytes:
    """Loud multi-formant PCM, well over any adaptive threshold."""
    rate = 16000
    num = int(rate * seconds)
    samples = []
    for i in range(num):
        t = i / rate
        val = (math.sin(2 * math.pi * 220 * t) * 0.4
               + math.sin(2 * math.pi * 700 * t) * 0.3
               + math.sin(2 * math.pi * 2400 * t) * 0.15) * 12000.0
        samples.append(max(-32767, min(32767, int(val))))
    return struct.pack(f"<{len(samples)}h", *samples)


def quiet_chunk(seconds: float = 0.1) -> bytes:
    """Near-silence: a low ambient hum, under the 650 energy threshold."""
    rate = 16000
    num = int(rate * seconds)
    samples = [max(-32767, min(32767, int(40 * math.sin(2 * math.pi * 100 * (i / rate)))))
               for i in range(num)]
    return struct.pack(f"<{len(samples)}h", *samples)


# --- the fake LLM: deterministic bibles, scripts, and miner passes ---

SCRIPT_JSON = {
    "title": "The Silent Room Protocol",
    "layout_type": "two-columns",
    "panels": [
        {"panel_number": 1, "scene_description": "wide shot, glowing console",
         "narration": "Meanwhile...", "speech_balloons": [{"character": "Agent Banjo", "text": "Room notes arrived."}],
         "sound_effect": "HUMMMM"},
        {"panel_number": 2, "scene_description": "close-up, bewildered cyborg",
         "narration": None, "speech_balloons": [{"character": "Doctor Rutabaga", "text": "Then we draw."}],
         "sound_effect": "KLANG!"},
    ],
    "lines_wanted_next": 1,
}


async def fake_complete_json(system_prompt, user_prompt, base_url=""):
    """One fake for the three callers: arc bibles, comic scripts, miner passes.
    Bibles count themselves, so two devices never share a series."""
    if "Invent a completely new comic series" in user_prompt:
        n = fake_complete_json.bibles[0]
        fake_complete_json.bibles[0] = n + 1
        return {"title": f"Mock Series #{n + 1}", "genre": f"genre {n + 1}",
                "premise": f"premise {n + 1}", "tone": "deadpan", "art_style": "ink",
                "cast": ["Agent Banjo: optimistic"]}
    if "sentences" in user_prompt and "For EACH" in user_prompt:
        inputs = [line.split(". ", 1)[-1] for line in user_prompt.splitlines()
                  if line.strip()[:1].isdigit()]
        cleaned = [item.split("] ", 1)[-1] if item.startswith("[") else item for item in inputs]
        return {"sentences": [{"vibe": "curious", "energy": "medium", "content": c} for c in cleaned]}
    return json.loads(json.dumps(SCRIPT_JSON))


fake_complete_json.bibles = [0]


@pytest.fixture
def fake_llm():
    fake_complete_json.bibles = [0]
    with patch("server.llm.complete_json", fake_complete_json):
        yield fake_complete_json


async def drain_and_store(device_id: str, token: str) -> None:
    """Complete one device's audio chain by hand: batch -> transcribe -> mine -> store."""
    pipe = pipelines.for_device(device_id)
    batch = pipe.transcriber.take()
    if not batch:
        return
    result = await asyncio.to_thread(pipe.transcriber.transcribe_batch, *batch)
    if result:
        await main_module.mine_and_store(*result, device_id=device_id)


def connect_stream(client: TestClient, device_id: str = None, token: str = None):
    """A /ws/audio connection the way the simulator presents identity."""
    url = "ws://test/ws/audio" + (f"?device_id={device_id}&device_token={token}" if device_id else "")
    return client.websocket_connect(url)


# --- V1: interleaved streams, per-device attribution, independent floors ---

def test_v1_interleaved_streams_stay_attributed_with_independent_floors(client, devices, fake_llm):
    """Two rooms interleaved chunk-by-chunk into the same server: speech in
    room B must never bleed into room A's VAD, batch, or transcripts, and a
    quiet room's noise floor must not learn a loud neighbor's energy."""
    (a_id, a_token), (b_id, b_token) = devices
    with connect_stream(client, a_id, a_token) as ws_a, connect_stream(client, b_id, b_token) as ws_b:
        # Phase 1: only room B speaks. Interleaved with A's quiet chunks, B's
        # energy must not start A's VAD or lift A's noise floor.
        for _ in range(20):
            ws_a.send_bytes(quiet_chunk())
            ws_b.send_bytes(speech_chunk())
        a, b = pipelines.for_device(a_id), pipelines.for_device(b_id)
        assert a.vad.is_speaking is False  # quiet room heard no speech at all
        assert b.vad.is_speaking is True
        assert a.vad._ambient_noise < 200.0  # drifted toward its own quiet room
        assert a.vad._ambient_noise < b.vad._ambient_noise

        # Phase 2: the rooms swap — now only A speaks. B's trailing pad (0.9s
        # in) closes its phase-1 segment; A's speech must never enter B's lane.
        for _ in range(20):
            ws_a.send_bytes(speech_chunk())
            ws_b.send_bytes(quiet_chunk())
        assert a.vad.is_speaking is True and b.vad.is_speaking is False

        # Both finish their sentences (trailing pad); both segments emit.
        for _ in range(12):
            ws_a.send_bytes(quiet_chunk())
            ws_b.send_bytes(quiet_chunk())
        assert a.vad.is_speaking is False and b.vad.is_speaking is False

    # Each room's segment carries its own device through the whole chain.
    for device_id, _ in devices:
        asyncio.run(drain_and_store(device_id, ""))
    rows_a = st.db.get_unprocessed_transcripts(a_id)
    rows_b = st.db.get_unprocessed_transcripts(b_id)
    assert rows_a and all(r["device_id"] == a_id for r in rows_a)
    assert rows_b and all(r["device_id"] == b_id for r in rows_b)


def test_v1_tokenless_stream_is_the_default_device(client, fake_llm):
    """A tokenless client (every pre-feature deployment, current firmware)
    binds the auto 'default' device, and its audio is attributed there too."""
    with connect_stream(client) as ws:
        for _ in range(12):
            ws.send_bytes(speech_chunk())
        for _ in range(12):
            ws.send_bytes(quiet_chunk())
        body = client.get("/api/devices").json()
    assert [d["id"] for d in body["devices"]] == ["default"]
    assert len(body["devices"][0]["connections"]) == 1
    asyncio.run(drain_and_store("default", ""))
    rows = st.db.get_unprocessed_transcripts("default")
    assert rows and all(r["device_id"] == "default" for r in rows)


# --- V2: each device holds its own ACTIVE arc ---

def test_v2_two_devices_hold_two_active_arcs(client, devices, fake_llm, monkeypatch):
    """Creating device B's arc must not conclude device A's — the old
    create_arc() concluded every ACTIVE arc in the house."""
    (a_id, _), (b_id, _) = devices
    # One-panel arcs: B's second cycle completes its first arc and seeds a
    # replacement, the tightest way to exercise the scoped conclusion.
    monkeypatch.setattr(main_module.settings, "ARC_MIN_PANELS", 1)
    monkeypatch.setattr(main_module.settings, "ARC_MAX_PANELS", 1)
    asyncio.run(main_module.run_comic_generation_cycle(a_id))
    asyncio.run(main_module.run_comic_generation_cycle(b_id))

    arc_a, arc_b = st.db.get_active_arc(a_id), st.db.get_active_arc(b_id)
    assert arc_a and arc_b and arc_a["id"] != arc_b["id"]
    assert arc_a["device_id"] == a_id and arc_b["device_id"] == b_id
    assert arc_a["status"] == "ACTIVE" and arc_b["status"] == "ACTIVE"

    # B completing its arc and starting a NEW one still leaves A's untouched.
    asyncio.run(main_module.run_comic_generation_cycle(b_id))
    assert st.db.get_active_arc(a_id)["id"] == arc_a["id"]
    assert st.db.get_active_arc(b_id)["id"] != arc_b["id"]  # arc completed -> a new one

    arcs = st.db.get_arcs()
    assert [a["device_id"] for a in arcs] == [a_id, b_id, b_id]
    assert [a["status"] for a in arcs] == ["ACTIVE", "CONCLUDED", "ACTIVE"]


# --- V3: pre-feature databases upgrade in place ---

def test_v3_prefeature_rows_backfill_to_the_default_device():
    """A database from before the feature (no devices table, no device_id
    columns) gains the columns, the auto default device, and a backfill of
    every pre-existing row — no data rewritten, nothing left NULL."""
    path = Path(tempfile.mkdtemp()) / "prefeature.db"
    conn = __import__("sqlite3").connect(path)
    conn.executescript("""
        CREATE TABLE story_arcs (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            genre TEXT NOT NULL, premise TEXT NOT NULL, started_at REAL NOT NULL,
            target_panels INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE',
            climax_stage INTEGER NOT NULL DEFAULT 0, metadata_json TEXT DEFAULT '{}');
        CREATE TABLE transcripts (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
            duration_seconds REAL NOT NULL, scrambled_text TEXT NOT NULL, used_in_comic_id INTEGER DEFAULT NULL);
        CREATE TABLE comics (id INTEGER PRIMARY KEY AUTOINCREMENT, issue_number INTEGER NOT NULL UNIQUE,
            arc_id INTEGER, title TEXT NOT NULL, layout_type TEXT NOT NULL, panel_count INTEGER NOT NULL,
            created_at REAL NOT NULL, displayed_at REAL DEFAULT NULL, status TEXT NOT NULL DEFAULT 'STEALTH_HOLD',
            silence_seconds_when_displayed REAL DEFAULT NULL, script_json TEXT NOT NULL,
            image_path TEXT NOT NULL, bin_path TEXT NOT NULL);
        CREATE TABLE key_value (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    now = time.time()
    conn.execute("INSERT INTO story_arcs (title, genre, premise, started_at, target_panels) "
                 "VALUES ('The Old Room', 'mystery', 'one room', ?, 40)", (now,))
    conn.execute("INSERT INTO transcripts (timestamp, duration_seconds, scrambled_text) VALUES (?, 1.0, 'legacy line')", (now,))
    conn.commit()
    conn.close()

    monkey_db = st.DB_PATH
    st.DB_PATH = path
    try:
        st.init_db()
        assert st.db.get_device("default") is not None
        assert st.db.get_active_arc("default")["title"] == "The Old Room"
        assert st.db.get_unprocessed_transcripts("default")[0]["scrambled_text"] == "legacy line"
        for table in ("story_arcs", "transcripts", "comics"):
            with st.get_connection() as conn:
                remaining = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE device_id IS NULL").fetchone()[0]
            assert remaining == 0, table
    finally:
        st.DB_PATH = monkey_db


# --- V4: wrong identity is rejected, never a silent fallback ---

def test_v4_wrong_identity_is_rejected_on_both_transports(client, devices):
    """HTTP: 401. WebSocket: closed 4401 (the ASGI close code; a pre-accept
    denial surfaces to raw clients as Starlette's HTTP 403, exactly as the
    /ws/events gate has always behaved)."""
    (a_id, a_token), _ = devices
    assert client.get("/api/comics/current",
                      params={"device_id": a_id, "device_token": "wrong"}).status_code == 401
    assert client.get("/api/comics/current",
                      params={"device_id": "ghost", "device_token": "x"}).status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with connect_stream(client, a_id, "wrong"):
            pass
    with pytest.raises(WebSocketDisconnect):
        with connect_stream(client, "ghost", "x"):
            pass
    # Neither rejection may bind anything to the default device.
    body = client.get("/api/devices").json()
    default = body["devices"][0]
    assert default["id"] == "default" and default["connections"] == []


# --- V5: delivery is identity-aware ---

def test_v5_each_device_polls_its_own_current_comic(client, devices, fake_llm):
    (a_id, a_token), (b_id, b_token) = devices
    comic_a = asyncio.run(main_module.run_comic_generation_cycle(a_id))
    comic_b = asyncio.run(main_module.run_comic_generation_cycle(b_id))
    pipelines.for_device(a_id).dispatcher.force_dispatch()
    pipelines.for_device(b_id).dispatcher.force_dispatch()

    current_a = client.get("/api/comics/current", params={"device_id": a_id, "device_token": a_token}).json()
    current_b = client.get("/api/comics/current", params={"device_id": b_id, "device_token": b_token}).json()
    assert current_a["issue_number"] == comic_a["issue_number"]
    assert current_b["issue_number"] == comic_b["issue_number"]
    assert current_a["issue_number"] != current_b["issue_number"]
    assert current_a["device_id"] == a_id and current_b["device_id"] == b_id

    # The image polls (the firmware's current.png / current.bin) follow the same identity.
    png_a = client.get("/api/comics/current.png",
                       params={"device_id": a_id, "device_token": a_token})
    png_b = client.get("/api/comics/current.png",
                       params={"device_id": b_id, "device_token": b_token})
    assert png_a.status_code == png_b.status_code == 200

    # A brand-new device polls empty until its first comic displays — never B's page.
    fresh = client.post("/api/devices", json={"name": "Attic"}).json()
    assert client.get("/api/comics/current",
                      params={"device_id": fresh["id"], "device_token": fresh["token"]}).status_code == 404


# --- V6: events carry the device tag; clients act only on their own ---

def test_v6_refresh_events_carry_device_id(client, devices, fake_llm):
    (a_id, _), (b_id, _) = devices
    asyncio.run(main_module.run_comic_generation_cycle(a_id))
    payloads = []
    # The dispatcher captured its callback at construction (server/pipelines.py),
    # so the capture swaps the instance's own reference — the seam a test
    # subscriber would sit behind.
    dispatcher = pipelines.for_device(a_id).dispatcher
    dispatcher.on_dispatch = payloads.append
    dispatcher.force_dispatch()
    assert len(payloads) == 1
    assert payloads[0]["event"] == "SNEAKY_COMIC_REFRESH"
    assert payloads[0]["device_id"] == a_id


def test_v6_simulator_acts_only_on_its_own_tag():
    """The shipped client filter (simulator/reterminal_sim.py), pinned here:
    own tag refreshes, foreign tags are ignored, untagged events (an older
    server) keep driving the display, and a tokenless simulator owns 'default'."""
    spec = importlib.util.spec_from_file_location(
        "reterminal_sim", REPO_ROOT / "simulator" / "reterminal_sim.py")
    sim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sim)

    assert sim.is_own_refresh({"device_id": "living-room"}, "living-room") is True
    assert sim.is_own_refresh({"device_id": "studio"}, "living-room") is False
    assert sim.is_own_refresh({"device_id": "default"}, None) is True  # tokenless sim = default
    assert sim.is_own_refresh({"device_id": "studio"}, None) is False
    assert sim.is_own_refresh({"issue_number": 9}, "living-room") is True  # legacy server


# --- V7: concurrent generation serializes on the global lock ---

def test_v7_two_devices_due_at_once_get_unique_issues(client, devices, fake_llm):
    (a_id, _), (b_id, _) = devices

    async def both_at_once():
        return await asyncio.gather(
            main_module.run_comic_generation_cycle(a_id),
            main_module.run_comic_generation_cycle(b_id))

    comic_a, comic_b = asyncio.run(both_at_once())
    assert comic_a["issue_number"] != comic_b["issue_number"]
    assert comic_a["device_id"] == a_id and comic_b["device_id"] == b_id
    # One global sequence, however many rooms are writing it.
    numbers = [c["issue_number"] for c in st.db.get_recent_comics(limit=50)]
    assert len(numbers) == len(set(numbers))


def test_v7_scheduler_tick_is_per_device(client, devices, fake_llm):
    """One 30s pass evaluates due-ness per connected device: A's unprocessed
    lines never feed B's comic, and both rooms get theirs in the same tick."""
    (a_id, a_token), (b_id, b_token) = devices
    asyncio.run(main_module.mine_and_store("[CURIOUS • MEDIUM] room A line", 1.0, 2.0, a_id))
    pipelines.for_device(a_id).connected = True  # live-stream check the WS handler would set
    pipelines.for_device(b_id).connected = True
    await_tick = main_module.scheduler_tick(started=time.time() - 10**6)  # long past due
    asyncio.run(await_tick)

    for device_id in (a_id, b_id):
        arc = st.db.get_active_arc(device_id)
        assert arc and arc["device_id"] == device_id
        comics = st.db.get_recent_comics(limit=5, device_id=device_id)
        assert comics, device_id
        assert all(c["device_id"] == device_id for c in comics)
