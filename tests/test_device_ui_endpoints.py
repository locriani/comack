"""Device-dimension endpoints the web UI consumes (spec V8 — the UI groups by
device, never by house).

- GET /api/devices carries each device's own story state: its active_arc and
  the current_comic its display is showing. The dashboard renders one arc card
  and one preview card per device from exactly this payload; a device with no
  story yet reports nulls rather than borrowing another room's.
- GET /api/arcs carries device_id on every arc (dashboard labels, archive and
  reader device filters).
- GET /api/comics/history carries device_id on every row (the archive grid's
  device filter).

TestClient without the lifespan: no models are loaded and the scheduler never
runs, so seeded rows are the whole story. Devices are created through the API
(the registry path the settings UI uses); arcs and comics are seeded through
storage — save_comic inherits the arc's device, the same attribution path
production takes.
"""
import tempfile
from pathlib import Path

import pytest
import server.storage as st
from fastapi.testclient import TestClient

from server.main import app


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """Isolate per test like test_storage.py (the db singleton reads
    storage.DB_PATH at call time)."""
    monkeypatch.setattr(st, "DB_PATH", Path(tempfile.mkdtemp()) / "device_ui.db")
    st.init_db()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def register_device(client: TestClient, name: str) -> str:
    """Through the API, as the settings UI does; returns the server-issued id."""
    return client.post("/api/devices", json={"name": name}).json()["id"]


def seed_displayed_comic(device_id: str, issue_number: int, title: str) -> None:
    """One arc owning one displayed issue, through the production save path."""
    arc_id = st.db.create_arc(
        title=f"{title} arc", genre="slice of life", premise=f"The {device_id} story.",
        target_panels=6, device_id=device_id)
    comic_id = st.db.save_comic(
        issue_number=issue_number, arc_id=arc_id, title=title, layout_type="single",
        panel_count=2, script_json={}, image_path="x.png", bin_path="x.bin")
    st.db.mark_comic_displayed(comic_id, silence_seconds=1.0)


class TestDevicesStoryState:
    def test_each_device_reports_its_own_arc_and_comic(self, client):
        living = register_device(client, "Living Room")
        den = register_device(client, "Den")
        seed_displayed_comic(living, 1, "The Toaster Rebellion")
        seed_displayed_comic(den, 2, "Basement Ambient")

        devices = {d["id"]: d for d in client.get("/api/devices").json()["devices"]}

        assert devices[living]["active_arc"]["title"] == "The Toaster Rebellion arc"
        assert devices[den]["active_arc"]["title"] == "Basement Ambient arc"
        assert devices[living]["current_comic"]["issue_number"] == 1
        assert devices[den]["current_comic"]["issue_number"] == 2
        # attribution cannot drift: every reported row carries its device
        assert devices[living]["current_comic"]["device_id"] == living
        assert devices[den]["current_comic"]["device_id"] == den

    def test_a_device_with_no_story_reports_nulls(self, client):
        register_device(client, "Attic")  # never generated, never displayed

        devices = client.get("/api/devices").json()["devices"]
        attic = next(d for d in devices if d["name"] == "Attic")

        assert attic["active_arc"] is None
        assert attic["current_comic"] is None

    def test_the_default_device_appears_with_its_story_state(self, client):
        seed_displayed_comic("default", 7, "House Issue")

        devices = client.get("/api/devices").json()["devices"]
        default = next(d for d in devices if d["id"] == "default")

        assert default["current_comic"]["issue_number"] == 7
        assert default["active_arc"]["title"] == "House Issue arc"

    def test_concluded_arcs_never_show_as_active(self, client):
        living = register_device(client, "Living Room")
        arc_id = st.db.create_arc(title="Old tale", genre="drama", premise="Done.",
                                  target_panels=4, device_id=living)
        st.db.conclude_active_arcs(living)

        devices = {d["id"]: d for d in client.get("/api/devices").json()["devices"]}

        assert devices[living]["active_arc"] is None
        assert arc_id  # the arc exists, concluded — just not active


class TestArcsCarryDevices:
    def test_every_arc_names_its_device(self, client):
        living = register_device(client, "Living Room")
        den = register_device(client, "Den")
        st.db.create_arc(title="Living story", genre="a", premise="p", target_panels=4, device_id=living)
        st.db.create_arc(title="Den story", genre="b", premise="q", target_panels=4, device_id=den)

        arcs = {a["title"]: a for a in client.get("/api/arcs").json()}

        assert arcs["Living story"]["device_id"] == living
        assert arcs["Den story"]["device_id"] == den


class TestHistoryCarriesDevices:
    def test_every_issue_row_names_its_device(self, client):
        living = register_device(client, "Living Room")
        den = register_device(client, "Den")
        seed_displayed_comic(living, 1, "Living issue")
        seed_displayed_comic(den, 2, "Den issue")

        rows = client.get("/api/comics/history?limit=200").json()
        by_title = {r["title"]: r for r in rows}

        assert by_title["Living issue"]["device_id"] == living
        assert by_title["Den issue"]["device_id"] == den
