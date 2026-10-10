import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
import server.storage as st
from server import llm as llm_client
from server.storage import DEFAULT_DEVICE_ID
from server.story.narrative_engine import engine_for

BIBLE = {"title": "Moss Patrol", "genre": "pastoral superhero", "premise": "Snails guard a pond.",
         "tone": "earnest kids' adventure", "art_style": "rubber-hose cartoon", "cast": ["Shelly: the leader"]}

SCRIPT_REPLY = {"title": "The Rains", "layout_type": "two-columns",
                "panels": [
                    {"panel_number": 1, "scene_description": "wide shot of the pond", "narration": "Meanwhile...",
                     "speech_balloons": [{"character": "Shelly", "text": "The pond rises."}], "sound_effect": None},
                    {"panel_number": 2, "scene_description": "close up on a snail", "narration": None,
                     "speech_balloons": [{"character": "Gill", "text": "Hold the line."}], "sound_effect": "SPLASH"}],
                "lines_wanted_next": 5}


def fresh_db(monkeypatch):
    monkeypatch.setattr(st, "DB_PATH", Path(tempfile.mkdtemp()) / "t.db")
    st.Database()


def new_arc(reply, monkeypatch, device_id=DEFAULT_DEVICE_ID):
    fresh_db(monkeypatch)
    past_arc = st.db.create_arc("Old Show", "space noir", "A detective in orbit.", 10, device_id)
    st.db.conclude_arc(past_arc)
    llm = AsyncMock(return_value=reply)
    with patch.object(llm_client, "complete_json", llm):
        return asyncio.run(engine_for(device_id).get_or_create_active_arc()), llm


def test_each_new_arc_is_an_llm_invented_series_unlike_the_last(monkeypatch):
    arc, llm = new_arc(BIBLE, monkeypatch)
    assert (arc["title"], arc["genre"], arc["premise"]) == (BIBLE["title"], BIBLE["genre"], BIBLE["premise"])
    assert json.loads(arc["metadata_json"]) == {"tone": "earnest kids' adventure", "art_style": "rubber-hose cartoon",
                                                "cast": ["Shelly: the leader"]}
    assert "Old Show (space noir)" in llm.await_args.args[1]   # told what not to repeat


def test_no_arc_without_a_complete_bible(monkeypatch):
    with pytest.raises(RuntimeError):
        new_arc({**BIBLE, "tone": ""}, monkeypatch)
    assert st.db.get_active_arc(DEFAULT_DEVICE_ID) is None


def test_two_devices_hold_two_active_arcs_at_once(monkeypatch):
    """Spec V2: the one-active-arc rule is per device — A's and B's coexist,
    and each device's query sees only its own."""
    fresh_db(monkeypatch)
    st.db.create_device("living-room", "Living Room", "hash-a")
    st.db.create_device("studio", "Studio", "hash-b")
    a = st.db.create_arc("Orbit Noir", "space noir", "A detective in orbit.", 10, "living-room")
    b = st.db.create_arc("Pond Watch", "pastoral", "Snails guard a pond.", 10, "studio")

    assert st.db.get_active_arc("living-room")["id"] == a
    assert st.db.get_active_arc("studio")["id"] == b


def test_creating_bs_arc_leaves_as_active(monkeypatch):
    """Spec V2: device B's first arc no longer concludes device A's story —
    conclusion is scoped to the device that asked for it."""
    fresh_db(monkeypatch)
    st.db.create_device("living-room", "Living Room", "hash-a")
    st.db.create_device("studio", "Studio", "hash-b")
    a = st.db.create_arc("Orbit Noir", "space noir", "A detective in orbit.", 10, "living-room")
    b = st.db.create_arc("Pond Watch", "pastoral", "Snails guard a pond.", 10, "studio")

    assert st.db.get_active_arc("living-room")["id"] == a

    # within one device the old rule still holds: a new arc concludes that device's previous one
    a2 = st.db.create_arc("Orbit Noir II", "cyberpunk", "The detective reboots.", 10, "living-room")
    assert st.db.get_active_arc("living-room")["id"] == a2
    assert st.db.get_active_arc("studio")["id"] == b   # B's arc survives A's second seeding


def test_engines_seed_arcs_for_their_own_devices(monkeypatch):
    """One engine per device: each seeds an arc bound to its own device, and
    neither engine's seeding disturbs the other's."""
    fresh_db(monkeypatch)
    st.db.create_device("living-room", "Living Room", "hash-a")
    st.db.create_device("studio", "Studio", "hash-b")
    llm = AsyncMock(return_value=BIBLE)
    with patch.object(llm_client, "complete_json", llm):
        arc_a = asyncio.run(engine_for("living-room").get_or_create_active_arc())
        arc_b = asyncio.run(engine_for("studio").get_or_create_active_arc())

    assert arc_a["device_id"] == "living-room"
    assert arc_b["device_id"] == "studio"
    assert arc_a["id"] != arc_b["id"]


def test_story_continuity_stays_inside_its_device(monkeypatch):
    """A's second issue is written from A's arc history only — B's interleaved
    issue never enters A's story_so_far."""
    fresh_db(monkeypatch)
    st.db.create_device("living-room", "Living Room", "hash-a")
    st.db.create_device("studio", "Studio", "hash-b")
    a_script, b_script, a2_script = (dict(SCRIPT_REPLY, title=t) for t in ("A's Rains", "B's Comet", "A's Rains II"))
    # Each engine's first call invents its arc bible, then asks for the script —
    # A's second issue finds its arc active, so no bible call precedes it.
    llm = AsyncMock(side_effect=[BIBLE, a_script, BIBLE, b_script, a2_script])
    with patch.object(llm_client, "complete_json", llm):
        engine_a, engine_b = engine_for("living-room"), engine_for("studio")
        first_a = asyncio.run(engine_a.generate_comic_script([]))
        # the generation cycle saves the comic after rendering; the arc's history accumulates
        st.db.save_comic(first_a["issue_number"], first_a["arc_id"], first_a["title"],
                         first_a["layout_type"], first_a["panel_count"], first_a, "a", "b")
        second_b = asyncio.run(engine_b.generate_comic_script([]))
        st.db.save_comic(second_b["issue_number"], second_b["arc_id"], second_b["title"],
                         second_b["layout_type"], second_b["panel_count"], second_b, "a", "b")
        asyncio.run(engine_a.generate_comic_script([]))

    script_prompts = [call.args[1] for call in llm.await_args_list if "Room notes" in call.args[1]]
    assert len(script_prompts) == 3
    assert "Story So Far" not in script_prompts[0]          # A's first issue: no history yet
    assert "A's Rains" not in script_prompts[1]             # B never sees A's story
    assert "A's Rains" in script_prompts[2]                 # A continues its own arc
    assert "B's Comet" not in script_prompts[2]             # ...and never B's
