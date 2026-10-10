"""Identity-aware comic delivery and device-tagged events (spec D2, verification V5/V6).

Delivery (V5): /api/comics/current (and its .png/.bin variants) resolve the asking
device — a device-paired poll gets that device's latest DISPLAYED issue, a tokenless
caller (browser <img> tags, legacy firmware) gets the auto 'default' device's, and
identity presented but wrong is a 401, never a silent fallback. /api/comics/{n}/bin
stays unauthenticated: issue numbers are a global namespace and the tailnet is the
trust boundary (spec D2).

Events (V6): SNEAKY_COMIC_REFRESH carries device_id, so a client acts only on its
own tag; each device's pipeline owns its dispatcher slot behind its own silence
gate, so one device's reveal never waits on (or reveals for) another device's room.
/api/trigger is attributed to the triggering device.

DB isolation follows the test_storage.py convention: patch storage.DB_PATH to a
fresh file per test. Tests run without the lifespan (no models), so main.pipelines,
main._trigger_running and main.generation_lock are swapped for clean stand-ins per
test.
"""
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import server.storage as st
from fastapi.testclient import TestClient

import server.main as main_module
from server.audio.vad import AudioVAD
from server.devices import hash_device_token
from server.dispatcher.sneaky_dispatcher import SneakyDispatcher
from server.main import app
from server.pipelines import PipelineRegistry
from server.storage import DEFAULT_DEVICE_ID, db


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    monkeypatch.setattr(st, "DB_PATH", Path(tempfile.mkdtemp()) / "delivery.db")
    st.init_db()
    yield


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    """Handlers read main.pipelines / main._trigger_running by module-global name;
    swap in clean ones per test so device pipelines and dedup flags never leak."""
    monkeypatch.setattr(main_module, "pipelines", PipelineRegistry())
    monkeypatch.setattr(main_module, "_trigger_running", {})
    monkeypatch.setattr(main_module, "generation_lock", asyncio.Lock())
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def comics_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "COMICS_DIR", tmp_path)
    return tmp_path


def register(device_id, token):
    db.create_device(device_id, device_id.title(), hash_device_token(token))


def displayed_comic(device_id, issue_number):
    """A DISPLAYED row for one device, created the way the pipeline creates
    one: the comic inherits its arc's device."""
    arc = db.create_arc(f"Arc {device_id}", "noir", "p", 10, device_id)
    comic_id = db.save_comic(issue_number, arc, f"Issue {issue_number}", "splash", 1, {}, "a", "b")
    db.mark_comic_displayed(comic_id, 0.0)


def stealth_hold(device_id, issue_number):
    arc = db.create_arc(f"Held {device_id}", "noir", "p", 10, device_id)
    return db.save_comic(issue_number, arc, f"Held {issue_number}", "splash", 1, {}, "a", "b")


class TestIdentityAwareCurrent:
    def test_each_device_reads_its_own_issue(self, client):
        """Spec V5: device A never receives device B's comic."""
        register("living-room", "tok-a")
        register("studio", "tok-b")
        displayed_comic("living-room", 1)
        displayed_comic("studio", 2)

        a = client.get("/api/comics/current?device_id=living-room&device_token=tok-a")
        b = client.get("/api/comics/current?device_id=studio&device_token=tok-b")
        assert a.json()["issue_number"] == 1
        assert b.json()["issue_number"] == 2

    def test_tokenless_caller_reads_the_default_device(self, client):
        displayed_comic(DEFAULT_DEVICE_ID, 5)
        resp = client.get("/api/comics/current")
        assert resp.json()["issue_number"] == 5
        assert resp.json()["device_id"] == DEFAULT_DEVICE_ID

    def test_wrong_token_is_401_never_a_fallback(self, client):
        register("living-room", "tok-a")
        displayed_comic("living-room", 1)
        resp = client.get("/api/comics/current?device_id=living-room&device_token=wrong")
        assert resp.status_code == 401

    def test_unknown_device_and_partial_identity_are_401(self, client):
        register("living-room", "tok-a")
        for qs in ("?device_id=ghost&device_token=tok-a",
                   "?device_id=living-room",
                   "?device_token=tok-a"):
            assert client.get(f"/api/comics/current{qs}").status_code == 401, qs

    def test_default_device_with_no_displayed_comic_is_404(self, client):
        register("living-room", "tok-a")
        displayed_comic("living-room", 1)
        # A tokenless browser must not see living-room's issue just because default has none.
        assert client.get("/api/comics/current").status_code == 404

    def test_current_png_serves_the_asking_devices_issue(self, client, comics_dir):
        register("living-room", "tok-a")
        register("studio", "tok-b")
        displayed_comic("living-room", 1)
        displayed_comic("studio", 2)
        for issue in (1, 2):
            (comics_dir / f"issue_{issue:04d}.png").write_bytes(f"png-{issue}".encode())
            (comics_dir / f"issue_{issue:04d}.bin").write_bytes(f"bin-{issue}".encode())

        assert client.get("/api/comics/current.png?device_id=living-room&device_token=tok-a").content == b"png-1"
        assert client.get("/api/comics/current.png?device_id=studio&device_token=tok-b").content == b"png-2"
        assert client.get("/api/comics/current.bin?device_id=studio&device_token=tok-b").content == b"bin-2"

    def test_current_png_with_wrong_token_is_401(self, client, comics_dir):
        register("living-room", "tok-a")
        displayed_comic("living-room", 1)
        (comics_dir / "issue_0001.png").write_bytes(b"png-1")
        resp = client.get("/api/comics/current.png?device_id=living-room&device_token=wrong")
        assert resp.status_code == 401

    def test_tokenless_current_png_reads_the_default_device(self, client, comics_dir):
        displayed_comic(DEFAULT_DEVICE_ID, 5)
        (comics_dir / "issue_0005.png").write_bytes(b"png-5")
        assert client.get("/api/comics/current.png?t=1234").content == b"png-5"

    def test_issue_bin_stays_unauthenticated_and_global(self, client, comics_dir):
        """Spec D2: issue numbers are a global namespace — no identity required."""
        register("living-room", "tok-a")
        displayed_comic("living-room", 1)
        displayed_comic("studio", 2)
        (comics_dir / "issue_0001.bin").write_bytes(b"bin-1")

        resp = client.get("/api/comics/1/bin")
        assert resp.status_code == 200
        assert resp.content == b"bin-1"


class TestPerDevicePendingSlots:
    def test_slots_hold_their_own_comics(self):
        dispatcher_a = SneakyDispatcher(vad=AudioVAD(), device_id="living-room")
        dispatcher_b = SneakyDispatcher(vad=AudioVAD(), device_id="studio")
        dispatcher_a.queue_comic({"id": 1, "issue_number": 7, "title": "A"})
        dispatcher_b.queue_comic({"id": 2, "issue_number": 8, "title": "B"})

        assert dispatcher_a.pending_comic["issue_number"] == 7
        assert dispatcher_b.pending_comic["issue_number"] == 8

    def test_stealth_hold_fallback_never_adopts_another_devices_row(self):
        stealth_hold("living-room", 1)
        stealth_hold("studio", 2)
        dispatcher_a = SneakyDispatcher(vad=AudioVAD(), device_id="living-room")
        dispatcher_b = SneakyDispatcher(vad=AudioVAD(), device_id="studio")

        assert dispatcher_a.force_dispatch()["issue_number"] == 1
        assert dispatcher_b.force_dispatch()["issue_number"] == 2

    def test_status_carries_its_device(self):
        dispatcher = SneakyDispatcher(vad=AudioVAD(), device_id="studio")
        assert dispatcher.get_status()["device_id"] == "studio"


class TestDeviceTaggedEvents:
    def test_refresh_events_carry_their_device_and_no_credentials(self):
        events = []
        dispatcher_a = SneakyDispatcher(vad=AudioVAD(), device_id="living-room", on_dispatch=events.append)
        dispatcher_b = SneakyDispatcher(vad=AudioVAD(), device_id="studio", on_dispatch=events.append)
        dispatcher_a.queue_comic({"id": 1, "issue_number": 7, "title": "A"})
        dispatcher_b.queue_comic({"id": 2, "issue_number": 8, "title": "B"})
        dispatcher_a.force_dispatch()
        dispatcher_b.force_dispatch()

        assert [(e["event"], e["device_id"], e["issue_number"]) for e in events] == [
            ("SNEAKY_COMIC_REFRESH", "living-room", 7),
            ("SNEAKY_COMIC_REFRESH", "studio", 8),
        ]
        # Payload URLs stay path-only: the event feed reaches every subscriber,
        # so a device's token must never ride in a broadcast payload.
        assert all(e["bin_url"] == "/api/comics/current.bin" for e in events)
        assert all("tok-a" not in str(e) and "tok-b" not in str(e) for e in events)

    def test_client_acts_only_on_its_own_tag(self):
        """Spec V6, the simulator's contract: refresh only on this device's event."""
        events = []
        dispatcher_a = SneakyDispatcher(vad=AudioVAD(), device_id="living-room", on_dispatch=events.append)
        dispatcher_b = SneakyDispatcher(vad=AudioVAD(), device_id="studio", on_dispatch=events.append)
        dispatcher_b.queue_comic({"id": 2, "issue_number": 8, "title": "B"})
        dispatcher_b.force_dispatch()  # studio reveals first
        dispatcher_a.queue_comic({"id": 1, "issue_number": 7, "title": "A"})
        dispatcher_a.force_dispatch()  # then living-room

        my_device = "living-room"
        refreshed = [e["issue_number"] for e in events
                     if e["event"] == "SNEAKY_COMIC_REFRESH" and e["device_id"] == my_device]
        assert refreshed == [7]  # studio's reveal did not refresh this display


class TestTriggerAttribution:
    def test_trigger_reveals_the_triggering_device(self):
        stealth_hold("living-room", 1)
        stealth_hold("studio", 2)

        asyncio.run(main_module._trigger_cycle_and_dispatch("studio"))

        assert db.get_current_displayed_comic("studio")["issue_number"] == 2
        assert db.get_current_displayed_comic("living-room") is None  # untouched

    def test_a_device_refresh_in_flight_does_not_drop_another_devices(self):
        stealth_hold("living-room", 1)
        stealth_hold("studio", 2)
        main_module._trigger_running["living-room"] = True

        asyncio.run(main_module._trigger_cycle_and_dispatch("living-room"))  # dedup: no-op while in flight
        asyncio.run(main_module._trigger_cycle_and_dispatch("studio"))

        assert db.get_current_displayed_comic("living-room") is None
        assert db.get_current_displayed_comic("studio")["issue_number"] == 2

    def test_wrong_identity_on_the_endpoint_is_401(self, client):
        register("living-room", "tok-a")
        for qs in ("?device_id=living-room&device_token=wrong",
                   "?device_id=ghost&device_token=tok-a",
                   "?device_id=living-room"):
            resp = client.post(f"/api/trigger{qs}", json={"phrase": "alpha protocol"})
            assert resp.status_code == 401, qs

    def test_identity_less_trigger_still_ok(self, client):
        with patch.object(main_module, "_trigger_cycle_and_dispatch", new=AsyncMock()):
            resp = client.post("/api/trigger", json={"phrase": "alpha protocol"})
        assert resp.status_code == 200

    def test_paired_trigger_is_ok(self, client):
        register("living-room", "tok-a")
        with patch.object(main_module, "_trigger_cycle_and_dispatch", new=AsyncMock()):
            resp = client.post("/api/trigger?device_id=living-room&device_token=tok-a",
                               json={"phrase": "alpha protocol"})
        assert resp.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__])
