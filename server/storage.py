"""
SQLite database storage for transcripts, comic strips, story arcs, and system state.
"""
import hmac
import sqlite3
import json
import time
from typing import List, Dict, Any, Optional
from pathlib import Path
from server.config import DATA_DIR

DB_PATH = DATA_DIR / "comack.db"

# The auto device every tokenless connection binds to and every pre-feature row
# is backfilled to (spec D1/D4). Its token_hash is '' — it has no token by
# definition: identity is *presented* to reach any other device, never for this one.
DEFAULT_DEVICE_ID = "default"
DEFAULT_DEVICE_NAME = "Default"


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
        metadata_json TEXT DEFAULT '{}',
        device_id TEXT DEFAULT NULL -- owning device; NULL = pre-attribution row (backfilled)
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transcripts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        duration_seconds REAL NOT NULL,
        scrambled_text TEXT NOT NULL,
        used_in_comic_id INTEGER DEFAULT NULL,
        device_id TEXT DEFAULT NULL, -- owning device; NULL = pre-attribution row (backfilled)
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
        device_id TEXT DEFAULT NULL, -- owning device; NULL = pre-attribution row (backfilled)
        FOREIGN KEY (arc_id) REFERENCES story_arcs(id)
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS key_value (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS devices (
        id TEXT PRIMARY KEY, -- short server-issued id, e.g. "living-room"
        name TEXT NOT NULL, -- user-visible label, renamable
        token_hash TEXT NOT NULL, -- SHA-256; compared constant-time (like WEB_TOKEN)
        created_at REAL NOT NULL,
        last_seen_at REAL DEFAULT NULL
    )
    """)
    
    # Databases created before the multi-device feature have no device_id columns
    # (CREATE TABLE above only helps fresh files). ALTER is guarded so re-running
    # init_db stays idempotent; SQLite adds a nullable column cheaply, no rewrite.
    for table in ("story_arcs", "transcripts", "comics"):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "device_id" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN device_id TEXT")
    
    # Backfill (spec D4): the auto 'default' device owns every row that predates
    # per-device attribution, so a tokenless client on an upgraded database
    # behaves exactly as before. Runs every startup; a no-op once attributed.
    conn.execute(
        "INSERT OR IGNORE INTO devices (id, name, token_hash, created_at) VALUES (?, ?, '', ?)",
        (DEFAULT_DEVICE_ID, DEFAULT_DEVICE_NAME, time.time()),
    )
    for table in ("story_arcs", "transcripts", "comics"):
        conn.execute(f"UPDATE {table} SET device_id = ? WHERE device_id IS NULL", (DEFAULT_DEVICE_ID,))
    
    conn.commit()
    conn.close()


# issue_number is the global id (files, URLs); each arc is its own series numbered from #1.
COMIC_COLS = ("*, (SELECT COUNT(*) FROM comics c2 WHERE c2.arc_id IS comics.arc_id "
              "AND c2.issue_number <= comics.issue_number) AS series_issue")


class Database:
    def __init__(self):
        init_db()

    # --- Story Arc Methods ---
    def get_active_arc(self, device_id: str) -> Optional[Dict[str, Any]]:
        """The one active arc of this device — N devices hold N active arcs at once."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT *, (SELECT COALESCE(SUM(panel_count), 0) FROM comics WHERE arc_id = story_arcs.id) AS panels_used
            FROM story_arcs WHERE status = 'ACTIVE' AND device_id = ? ORDER BY id DESC LIMIT 1
        """, (device_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def create_arc(self, title: str, genre: str, premise: str, target_panels: int, device_id: str,
                   metadata: Dict[str, Any] = None) -> int:
        self.conclude_active_arcs(device_id)  # this device's previous arc — never another device's
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO story_arcs (title, genre, premise, started_at, target_panels, status, metadata_json, device_id)
            VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
        """, (title, genre, premise, time.time(), target_panels, json.dumps(metadata or {}), device_id))
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

    def conclude_arc(self, arc_id: int):
        """Conclude one arc and wipe the room lines it used:
        the transcript rows and each comic's copy in its script."""
        self._conclude_arcs("id = ?", (arc_id,))

    def conclude_active_arcs(self, device_id: str):
        """Conclude every ACTIVE arc owned by one device — the scoped form create_arc
        uses, so device B's new arc never ends device A's story."""
        self._conclude_arcs("status = 'ACTIVE' AND device_id = ?", (device_id,))

    def _conclude_arcs(self, where: str, params: tuple):
        """Shared conclusion body: wipe the arcs' transcript rows and each comic's
        copy of the dialogue in its script, then mark the arcs CONCLUDED."""
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
    def save_transcript(self, timestamp: float, duration_seconds: float, scrambled_text: str,
                        device_id: str) -> int:
        """Store one mined line, attributed to the device whose pipeline heard it.
        The device is required: a NULL row would be invisible to every device-scoped
        query until the next restart backfills it — a silent attribution bug."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO transcripts (timestamp, duration_seconds, scrambled_text, device_id)
            VALUES (?, ?, ?, ?)
        """, (timestamp, duration_seconds, scrambled_text, device_id))
        transcript_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return transcript_id

    def get_unprocessed_transcripts(self, device_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Unconsumed rows, oldest first. One device's lines when given (what a
        generation cycle consumes and what due-ness counts); every device's
        lines when no device is passed (debug surfaces)."""
        conn = get_connection()
        cursor = conn.cursor()
        if device_id:
            cursor.execute("""
                SELECT * FROM transcripts
                WHERE used_in_comic_id IS NULL AND device_id = ?
                ORDER BY timestamp ASC
            """, (device_id,))
        else:
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
        # The comic inherits its arc's device, so attribution cannot drift from the
        # story it continues (None only for a missing arc: pre-attribution semantics).
        arc = conn.execute("SELECT device_id FROM story_arcs WHERE id = ?", (arc_id,)).fetchone()
        device_id = arc["device_id"] if arc else None
        cursor.execute("""
            INSERT INTO comics (issue_number, arc_id, title, layout_type, panel_count, 
                                created_at, status, script_json, image_path, bin_path, device_id)
            VALUES (?, ?, ?, ?, ?, ?, 'STEALTH_HOLD', ?, ?, ?, ?)
        """, (issue_number, arc_id, title, layout_type, panel_count, time.time(),
              json.dumps(script_json), str(image_path), str(bin_path), device_id))
        comic_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return comic_id

    def get_stealth_hold_comic(self, device_id: str) -> Optional[Dict[str, Any]]:
        """This device's oldest held comic. Device-scoped so a per-device dispatcher
        slot can never pick up, and release onto, another device's reveal."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT {COMIC_COLS} FROM comics WHERE status = 'STEALTH_HOLD' AND device_id = ? "
                       f"ORDER BY id ASC LIMIT 1", (device_id,))
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

    def get_current_displayed_comic(self, device_id: str) -> Optional[Dict[str, Any]]:
        """This device's latest DISPLAYED issue — each panel shows its own comic."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT {COMIC_COLS} FROM comics WHERE status = 'DISPLAYED' AND device_id = ? "
                       f"ORDER BY displayed_at DESC LIMIT 1", (device_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_recent_comics(self, limit: int = 20, device_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Comics newest-first. One device's issues when given (what its display
        and due-ness read); the whole archive when no device is passed."""
        conn = get_connection()
        if device_id:
            rows = conn.execute(f"SELECT {COMIC_COLS} FROM comics WHERE device_id = ? "
                                f"ORDER BY issue_number DESC LIMIT ?", (device_id, limit)).fetchall()
        else:
            rows = conn.execute(f"SELECT {COMIC_COLS} FROM comics ORDER BY issue_number DESC LIMIT ?",
                                (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_comic(self, issue_number: int) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        row = conn.execute(f"SELECT {COMIC_COLS} FROM comics WHERE issue_number = ?", (issue_number,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_arcs(self) -> List[Dict[str, Any]]:
        """Every arc, oldest first, attributed to its device, each with its
        issues (no scripts) for the reader."""
        conn = get_connection()
        arcs = [dict(r) for r in conn.execute(
            "SELECT id, title, genre, premise, status, metadata_json, device_id FROM story_arcs ORDER BY id")]
        for arc in arcs:
            arc["comics"] = [dict(r) for r in conn.execute(
                "SELECT issue_number, title, layout_type, panel_count, status, created_at, "
                "ROW_NUMBER() OVER (ORDER BY issue_number) AS series_issue FROM comics "
                "WHERE arc_id = ? ORDER BY issue_number", (arc["id"],))]
        conn.close()
        return arcs

    def get_arc_comics(self, arc_id: int) -> List[Dict[str, Any]]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT {COMIC_COLS} FROM comics WHERE arc_id = ? ORDER BY issue_number ASC", (arc_id,))
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

    # --- Device Registry Methods ---
    # Persistence only: the token arrives pre-hashed and is never selected back
    # out (devices.py owns hashing; check_device_token_hash compares in place).
    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_devices(self) -> List[Dict[str, Any]]:
        """Every registered device, oldest first (the auto 'default' leads)."""
        conn = get_connection()
        rows = conn.execute("SELECT * FROM devices ORDER BY created_at, id").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def create_device(self, device_id: str, name: str, token_hash: str) -> None:
        conn = get_connection()
        conn.execute(
            "INSERT INTO devices (id, name, token_hash, created_at) VALUES (?, ?, ?, ?)",
            (device_id, name, token_hash, time.time()),
        )
        conn.commit()
        conn.close()

    def rename_device(self, device_id: str, name: str) -> bool:
        conn = get_connection()
        cursor = conn.execute("UPDATE devices SET name = ? WHERE id = ?", (name, device_id))
        conn.commit()
        changed = cursor.rowcount > 0
        conn.close()
        return changed

    def touch_device(self, device_id: str) -> None:
        conn = get_connection()
        conn.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (time.time(), device_id))
        conn.commit()
        conn.close()

    def check_device_token_hash(self, device_id: str, token_hash: str) -> bool:
        """Constant-time compare of a presented token's hash against the stored
        one; False for an unknown device (existence is not secret on the tailnet,
        the token itself is — and this never confirms a wrong token)."""
        conn = get_connection()
        row = conn.execute("SELECT token_hash FROM devices WHERE id = ?", (device_id,)).fetchone()
        conn.close()
        return row is not None and hmac.compare_digest(row["token_hash"], token_hash)


db = Database()
