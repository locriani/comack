"""
SQLite database storage for transcripts, comic strips, story arcs, and system state.
"""
import sqlite3
import json
import time
from typing import List, Dict, Any, Optional
from pathlib import Path
from server.config import DATA_DIR

DB_PATH = DATA_DIR / "comack.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS story_arcs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        genre TEXT NOT NULL,
        premise TEXT NOT NULL,
        started_at REAL NOT NULL,
        target_panels INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'ACTIVE', -- 'ACTIVE', 'CONCLUDED'
        climax_stage INTEGER NOT NULL DEFAULT 0,
        metadata_json TEXT DEFAULT '{}'
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transcripts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        duration_seconds REAL NOT NULL,
        scrambled_text TEXT NOT NULL,
        used_in_comic_id INTEGER DEFAULT NULL,
        FOREIGN KEY (used_in_comic_id) REFERENCES comics(id)
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS comics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue_number INTEGER NOT NULL UNIQUE,
        arc_id INTEGER,
        title TEXT NOT NULL,
        layout_type TEXT NOT NULL, -- a comic_compositor.LAYOUTS name
        panel_count INTEGER NOT NULL,
        created_at REAL NOT NULL,
        displayed_at REAL DEFAULT NULL,
        status TEXT NOT NULL DEFAULT 'STEALTH_HOLD', -- 'STEALTH_HOLD', 'DISPLAYED'
        silence_seconds_when_displayed REAL DEFAULT NULL,
        script_json TEXT NOT NULL,
        image_path TEXT NOT NULL,
        bin_path TEXT NOT NULL,
        FOREIGN KEY (arc_id) REFERENCES story_arcs(id)
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS key_value (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)
    
    conn.commit()
    conn.close()


class Database:
    def __init__(self):
        init_db()

    # --- Story Arc Methods ---
    def get_active_arc(self) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM story_arcs WHERE status = 'ACTIVE' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def create_arc(self, title: str, genre: str, premise: str, target_panels: int, metadata: Dict[str, Any] = None) -> int:
        self.conclude_arc()  # any previous active arc
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO story_arcs (title, genre, premise, started_at, target_panels, status, metadata_json)
            VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?)
        """, (title, genre, premise, time.time(), target_panels, json.dumps(metadata or {})))
        arc_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return arc_id

    def update_arc_climax(self, arc_id: int, stage: int):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE story_arcs SET climax_stage = ? WHERE id = ?", (stage, arc_id))
        conn.commit()
        conn.close()

    def rename_arc(self, arc_id: int, title: str):
        """Set the LLM-given title and mark the arc named."""
        conn = get_connection()
        conn.execute("UPDATE story_arcs SET title = ?, metadata_json = json_set(COALESCE(metadata_json, '{}'), '$.named', 1) "
                     "WHERE id = ?", (title, arc_id))
        conn.commit()
        conn.close()

    def conclude_arc(self, arc_id: Optional[int] = None):
        """Conclude an arc (default: every active one) and wipe the room lines it used:
        the transcript rows and each comic's copy in its script."""
        where, params = ("id = ?", (arc_id,)) if arc_id is not None else ("status = 'ACTIVE'", ())
        arcs = f"SELECT id FROM story_arcs WHERE {where}"
        conn = get_connection()
        conn.execute(f"DELETE FROM transcripts WHERE used_in_comic_id IN "
                     f"(SELECT id FROM comics WHERE arc_id IN ({arcs}))", params)
        conn.execute(f"UPDATE comics SET script_json = json_remove(script_json, '$.dialogue_used') "
                     f"WHERE arc_id IN ({arcs})", params)
        conn.execute(f"UPDATE story_arcs SET status = 'CONCLUDED' WHERE {where}", params)
        conn.commit()
        conn.close()

    # --- Transcript Methods ---
    def save_transcript(self, timestamp: float, duration_seconds: float, scrambled_text: str) -> int:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO transcripts (timestamp, duration_seconds, scrambled_text)
            VALUES (?, ?, ?)
        """, (timestamp, duration_seconds, scrambled_text))
        transcript_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return transcript_id

    def get_unprocessed_transcripts(self) -> List[Dict[str, Any]]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM transcripts 
            WHERE used_in_comic_id IS NULL 
            ORDER BY timestamp ASC
        """)
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def mark_transcripts_used(self, transcript_ids: List[int], comic_id: int):
        if not transcript_ids:
            return
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in transcript_ids)
        cursor.execute(f"""
            UPDATE transcripts SET used_in_comic_id = ? WHERE id IN ({placeholders})
        """, [comic_id] + transcript_ids)
        conn.commit()
        conn.close()

    # --- Comic Methods ---
    def get_next_issue_number(self) -> int:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COALESCE(MAX(issue_number), 0) + 1 FROM comics")
        next_num = cursor.fetchone()[0]
        conn.close()
        return next_num

    def save_comic(self, issue_number: int, arc_id: Optional[int], title: str, layout_type: str,
                   panel_count: int, script_json: Dict[str, Any], image_path: str, bin_path: str) -> int:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO comics (issue_number, arc_id, title, layout_type, panel_count, 
                                created_at, status, script_json, image_path, bin_path)
            VALUES (?, ?, ?, ?, ?, ?, 'STEALTH_HOLD', ?, ?, ?)
        """, (issue_number, arc_id, title, layout_type, panel_count, time.time(),
              json.dumps(script_json), str(image_path), str(bin_path)))
        comic_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return comic_id

    def get_stealth_hold_comic(self) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM comics WHERE status = 'STEALTH_HOLD' ORDER BY id ASC LIMIT 1
        """)
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def mark_comic_displayed(self, comic_id: int, silence_seconds: float):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE comics 
            SET status = 'DISPLAYED', displayed_at = ?, silence_seconds_when_displayed = ? 
            WHERE id = ?
        """, (time.time(), silence_seconds, comic_id))
        conn.commit()
        conn.close()

    def get_current_displayed_comic(self) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM comics WHERE status = 'DISPLAYED' ORDER BY displayed_at DESC LIMIT 1
        """)
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_recent_comics(self, limit: int = 20) -> List[Dict[str, Any]]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM comics ORDER BY issue_number DESC LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_comic(self, issue_number: int) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM comics WHERE issue_number = ?", (issue_number,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_arcs(self) -> List[Dict[str, Any]]:
        """Every arc, oldest first, each with its issues (no scripts) for the reader."""
        conn = get_connection()
        arcs = [dict(r) for r in conn.execute(
            "SELECT id, title, genre, premise, status FROM story_arcs ORDER BY id")]
        for arc in arcs:
            arc["comics"] = [dict(r) for r in conn.execute(
                "SELECT issue_number, title, layout_type, status, created_at FROM comics "
                "WHERE arc_id = ? ORDER BY issue_number", (arc["id"],))]
        conn.close()
        return arcs

    def get_arc_comics(self, arc_id: int) -> List[Dict[str, Any]]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM comics WHERE arc_id = ? ORDER BY issue_number ASC", (arc_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # --- Key-Value State Methods ---
    def set_state(self, key: str, value: str):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO key_value (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()

    def get_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM key_value WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else default


db = Database()
