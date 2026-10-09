"""POST /api/device/buttons: the device's fire-and-forget press reports.

The firmware has no token capability, so the route joins the tokenless
device-critical paths (server/auth.py DEVICE_PATH_PATTERNS) and must keep
working with WEB_TOKEN set. A valid press broadcasts BUTTON_PRESSED on
/ws/events; an unknown or missing button is a 400 and broadcasts nothing.
"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import main as main_module
from server.main import app


class TestDeviceButtonsRoute(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_valid_press_returns_ok_and_broadcasts(self):
        with patch.object(main_module, "broadcast_event") as broadcast:
            resp = self.client.post("/api/device/buttons", json={"button": "trigger"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True})
        broadcast.assert_called_once_with({"event": "BUTTON_PRESSED", "button": "trigger"})

    def test_every_button_name_broadcasts_itself(self):
        for button in ("forward", "back", "trigger"):
            with patch.object(main_module, "broadcast_event") as broadcast:
                resp = self.client.post("/api/device/buttons", json={"button": button})
            self.assertEqual(resp.status_code, 200)
            broadcast.assert_called_once_with({"event": "BUTTON_PRESSED", "button": button})

    def test_unknown_button_is_400_and_broadcasts_nothing(self):
        with patch.object(main_module, "broadcast_event") as broadcast:
            resp = self.client.post("/api/device/buttons", json={"button": "left"})
        self.assertEqual(resp.status_code, 400)
        broadcast.assert_not_called()

    def test_missing_button_is_400_and_broadcasts_nothing(self):
        with patch.object(main_module, "broadcast_event") as broadcast:
            resp = self.client.post("/api/device/buttons", json={})
        self.assertEqual(resp.status_code, 400)
        broadcast.assert_not_called()

    def test_press_reaches_the_event_feed(self):
        class FakeDispatcher:
            def get_status(self):
                return {"is_speaking": False}

        with patch.object(main_module, "sneaky_dispatcher", FakeDispatcher()):
            with self.client.websocket_connect("/ws/events") as ws:
                self.assertEqual(ws.receive_json()["event"], "INIT_SNAPSHOT")
                resp = self.client.post("/api/device/buttons", json={"button": "forward"})
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(ws.receive_json(), {"event": "BUTTON_PRESSED", "button": "forward"})


if __name__ == "__main__":
    unittest.main()
