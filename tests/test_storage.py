import json
import tempfile
from pathlib import Path
import server.storage as st


def test_each_arc_numbers_its_issues_from_one(monkeypatch):
    monkeypatch.setattr(st, "DB_PATH", Path(tempfile.mkdtemp()) / "t.db")
    db = st.Database()
    first = db.create_arc("A", "A", "p", 10)
    for n in (1, 2):
        db.save_comic(n, first, f"A{n}", "splash", 1, {}, "a", "b")
    second = db.create_arc("B", "B", "q", 10)
    db.save_comic(3, second, "B1", "splash", 1, {}, "a", "b")
    db.save_comic(4, second, "B2", "splash", 3, {}, "a", "b")
    assert db.get_active_arc()["panels_used"] == 4   # this arc's panels only

    assert [c["series_issue"] for c in db.get_recent_comics()] == [2, 1, 2, 1]
    assert db.get_comic(3)["series_issue"] == 1
    assert [[c["series_issue"] for c in a["comics"]] for a in db.get_arcs()] == [[1, 2], [1, 2]]
    assert db.get_next_issue_number() == 5   # the global id keeps counting


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
