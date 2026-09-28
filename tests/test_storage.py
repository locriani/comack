import json
import tempfile
from pathlib import Path
import server.storage as st


def test_concluding_an_arc_wipes_its_lines(monkeypatch):
    monkeypatch.setattr(st, "DB_PATH", Path(tempfile.mkdtemp()) / "t.db")
    db = st.Database()
    arc = db.create_arc("Noir", "Noir", "p", 10)
    comic = db.save_comic(1, arc, "First", "splash", 1, {"panels": [], "dialogue_used": ["[X • LOW] secret"]}, "a", "b")
    used = db.save_transcript(0, 1, "[X • LOW] secret")
    db.mark_transcripts_used([used], comic)
    db.save_transcript(0, 1, "[Y • HIGH] not yet used")

    db.create_arc("Next", "Next", "q", 10)  # concludes the first arc

    assert [t["scrambled_text"] for t in db.get_unprocessed_transcripts()] == ["[Y • HIGH] not yet used"]
    conn = st.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0] == 1
    script = json.loads(db.get_arc_comics(arc)[0]["script_json"])
    assert "dialogue_used" not in script and script["panels"] == []
    assert conn.execute("SELECT status FROM story_arcs WHERE id = ?", (arc,)).fetchone()[0] == "CONCLUDED"
