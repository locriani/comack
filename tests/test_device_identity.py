"""Device identity: resolve_device's three outcomes and the startup backfill.

The resolution contract (spec D1, verification criterion 4): a valid pair
binds that device; absent identity binds the auto 'default' device; identity
presented but wrong — unknown id, bad token, half an identity — is None, the
caller's signal to close 4401. Never a silent fallback to default: a mistyped
token must not quietly route one room's audio into another room's story.

The migration contract (spec D4, criterion 3): a database created before this
feature (no devices table, no device_id columns) upgrades in place at startup —
the auto 'default' device appears and every pre-existing row is backfilled to
it, so a tokenless client on an upgraded database behaves exactly as before.

DB isolation follows the test_storage.py convention: patch storage.DB_PATH to
a fresh file per test.
"""
import sqlite3
import tempfile
from pathlib import Path

import pytest

import server.storage as st
from server.devices import (
    generate_device_token,
    hash_device_token,
    public_device,
    resolve_device,
    slugify_device_id,
    unique_device_id,
)
from server.storage import DEFAULT_DEVICE_ID, db

# The pre-feature schema (what shipped before the multi-device work): no
# devices table, no device_id anywhere. Seeding it verbatim exercises the
# ALTER + backfill path a real upgraded deployment takes.
OLD_DDL = """
CREATE TABLE story_arcs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, genre TEXT NOT NULL, premise TEXT NOT NULL,
    started_at REAL NOT NULL, target_panels INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE', climax_stage INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT DEFAULT '{}'
);
CREATE TABLE comics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_number INTEGER NOT NULL UNIQUE, arc_id INTEGER, title TEXT NOT NULL,
    layout_type TEXT NOT NULL, panel_count INTEGER NOT NULL, created_at REAL NOT NULL,
    displayed_at REAL DEFAULT NULL, status TEXT NOT NULL DEFAULT 'STEALTH_HOLD',
    silence_seconds_when_displayed REAL DEFAULT NULL,
    script_json TEXT NOT NULL, image_path TEXT NOT NULL, bin_path TEXT NOT NULL,
    FOREIGN KEY (arc_id) REFERENCES story_arcs(id)
);
CREATE TABLE transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL, duration_seconds REAL NOT NULL, scrambled_text TEXT NOT NULL,
    used_in_comic_id INTEGER DEFAULT NULL,
    FOREIGN KEY (used_in_comic_id) REFERENCES comics(id)
);
CREATE TABLE key_value (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@pytest.fixture
def isolated_db(monkeypatch):
    monkeypatch.setattr(st, "DB_PATH", Path(tempfile.mkdtemp()) / "identity.db")
    st.init_db()
    yield


def register(id="living-room", name="Living Room", token="tok-abc123"):
    db.create_device(id, name, hash_device_token(token))
    return id, token


class TestResolveDevice:
    def test_absent_identity_binds_the_default_device(self, isolated_db):
        device = resolve_device(None, None)
        assert device is not None
        assert device["id"] == DEFAULT_DEVICE_ID

    def test_empty_params_are_absent_identity(self, isolated_db):
        # A client sending empty strings has presented nothing.
        assert resolve_device("", "")["id"] == DEFAULT_DEVICE_ID

    def test_valid_pair_binds_that_device(self, isolated_db):
        register(token="right-token")
        device = resolve_device("living-room", "right-token")
        assert device is not None
        assert device["id"] == "living-room"
        assert device["name"] == "Living Room"

    def test_wrong_token_is_none_never_default(self, isolated_db):
        register(token="right-token")
        assert resolve_device("living-room", "wrong-token") is None

    def test_unknown_device_is_none(self, isolated_db):
        register()
        assert resolve_device("ghost-room", "tok-abc123") is None

    def test_id_without_token_is_none(self, isolated_db):
        register()
        assert resolve_device("living-room", None) is None

    def test_token_without_id_is_none(self, isolated_db):
        register()
        assert resolve_device(None, "tok-abc123") is None

    def test_the_default_device_has_no_token_to_present(self, isolated_db):
        # Its hash is '': reachable only by presenting no identity at all.
        assert resolve_device(DEFAULT_DEVICE_ID, "anything") is None


class TestMigrationBackfill:
    @pytest.fixture
    def upgraded_db(self, monkeypatch):
        """A seeded pre-feature database with one row in each attributed table."""
        path = Path(tempfile.mkdtemp()) / "pre-feature.db"
        conn = sqlite3.connect(path)
        conn.executescript(OLD_DDL)
        conn.execute(
            "INSERT INTO story_arcs (title, genre, premise, started_at, target_panels) "
            "VALUES ('The Great Sleep', 'noir', 'p', 0, 10)"
        )
        conn.execute(
            "INSERT INTO comics (issue_number, arc_id, title, layout_type, panel_count, "
            "created_at, script_json, image_path, bin_path) "
            "VALUES (1, 1, 'First', 'splash', 1, 0, '{}', 'a', 'b')"
        )
        conn.execute("INSERT INTO transcripts (timestamp, duration_seconds, scrambled_text) VALUES (0, 1, 'line')")
        conn.commit()
        conn.close()
        monkeypatch.setattr(st, "DB_PATH", path)
        return path

    def test_startup_creates_the_default_device_and_backfills(self, upgraded_db):
        st.init_db()
        assert db.get_device(DEFAULT_DEVICE_ID)["name"] == "Default"
        assert db.get_device(DEFAULT_DEVICE_ID)["token_hash"] == ""  # tokenless by definition
        conn = sqlite3.connect(upgraded_db)
        for table in ("story_arcs", "transcripts", "comics"):
            unattributed = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE device_id IS NULL").fetchone()[0]
            attributed = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE device_id = ?", (DEFAULT_DEVICE_ID,)
            ).fetchone()[0]
            assert (unattributed, attributed) == (0, 1), table
        conn.close()

    def test_migration_is_idempotent(self, upgraded_db):
        st.init_db()
        st.init_db()  # second boot: no duplicate default, no error
        assert len(db.get_devices()) == 1

    def test_registered_devices_survive_reinit(self, upgraded_db):
        st.init_db()
        register(id="den", name="Den", token="t")
        st.init_db()  # a later boot must not disturb registered devices
        assert [d["id"] for d in db.get_devices()] == [DEFAULT_DEVICE_ID, "den"]

    def test_fresh_database_gets_the_default_device(self, isolated_db):
        assert [d["id"] for d in db.get_devices()] == [DEFAULT_DEVICE_ID]


class TestTokenMachinery:
    def test_generated_tokens_are_unique_and_long(self):
        tokens = {generate_device_token() for _ in range(100)}
        assert len(tokens) == 100
        assert all(len(t) >= 32 for t in tokens)

    def test_hash_is_stable_and_not_the_token(self):
        assert hash_device_token("tok") == hash_device_token("tok")
        assert hash_device_token("tok") != "tok"

    def test_storage_compares_hashes_constant_time(self, isolated_db):
        register(token="right")
        assert db.check_device_token_hash("living-room", hash_device_token("right"))
        assert not db.check_device_token_hash("living-room", hash_device_token("wrong"))
        assert not db.check_device_token_hash("ghost", hash_device_token("right"))


class TestDeviceIds:
    def test_slug_from_name(self):
        assert slugify_device_id("Living Room!") == "living-room"
        assert slugify_device_id("  Den  ") == "den"

    def test_unsluggable_names_still_get_an_id(self):
        assert slugify_device_id("客厅") == ""

    def test_unique_ids_dedupe(self, isolated_db):
        register(name="Living Room")  # occupies "living-room"
        assert unique_device_id("Living Room") == "living-room-2"
        assert unique_device_id("Den") == "den"

    def test_unique_id_never_collides_with_default(self, isolated_db):
        assert unique_device_id("Default") == "default-2"  # the auto device owns 'default'


class TestPublicDevice:
    def test_never_carries_the_token_hash(self, isolated_db):
        register(token="secret")
        public = public_device(db.get_device("living-room"))
        assert "token_hash" not in public
        assert "secret" not in str(public)

    def test_times_render_iso_and_null_last_seen_stays_null(self, isolated_db):
        register()
        public = public_device(db.get_device("living-room"))
        assert public["created_at"].endswith("Z")
        assert public["last_seen_at"] is None
        db.touch_device("living-room")
        assert public_device(db.get_device("living-room"))["last_seen_at"].endswith("Z")
