import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
import server.storage as st
from server import llm as llm_client
from server.story.narrative_engine import narrative_engine

BIBLE = {"title": "Moss Patrol", "genre": "pastoral superhero", "premise": "Snails guard a pond.",
         "tone": "earnest kids' adventure", "art_style": "rubber-hose cartoon", "cast": ["Shelly: the leader"]}


def new_arc(reply, monkeypatch):
    monkeypatch.setattr(st, "DB_PATH", Path(tempfile.mkdtemp()) / "t.db")
    st.Database().create_arc("Old Show", "space noir", "A detective in orbit.", 10)
    st.db.conclude_arc()
    llm = AsyncMock(return_value=reply)
    with patch.object(llm_client, "complete_json", llm):
        return asyncio.run(narrative_engine.get_or_create_active_arc()), llm


def test_each_new_arc_is_an_llm_invented_series_unlike_the_last(monkeypatch):
    arc, llm = new_arc(BIBLE, monkeypatch)
    assert (arc["title"], arc["genre"], arc["premise"]) == ("Moss Patrol", "pastoral superhero", "Snails guard a pond.")
    assert json.loads(arc["metadata_json"]) == {"tone": "earnest kids' adventure", "art_style": "rubber-hose cartoon",
                                                "cast": ["Shelly: the leader"]}
    assert "Old Show (space noir)" in llm.await_args.args[1]   # told what not to repeat


def test_no_arc_without_a_complete_bible(monkeypatch):
    with pytest.raises(RuntimeError):
        new_arc({**BIBLE, "tone": ""}, monkeypatch)
    assert st.db.get_active_arc() is None
