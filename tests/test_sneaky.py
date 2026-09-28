import asyncio
import time
import unittest
from unittest.mock import patch
from server.audio.vad import AudioVAD
from server.config import settings
from server.dispatcher.sneaky_dispatcher import SneakyDispatcher

class TestSneakyDispatcher(unittest.TestCase):
    def test_sneaky_dispatcher_state(self):
        vad = AudioVAD()
        dispatched = []
        
        def on_disp(payload):
            dispatched.append(payload)
            
        dispatcher = SneakyDispatcher(vad=vad, on_dispatch=on_disp)
        
        mock_comic = {
            "id": 1,
            "issue_number": 42,
            "title": "Stealth Test",
            "created_at": time.time()
        }
        
        dispatcher.queue_comic(mock_comic)
        status = dispatcher.get_status()
        
        self.assertTrue(status["has_stealth_comic"])
        self.assertEqual(status["pending_issue_number"], 42)
        
        # Test force dispatch
        result = dispatcher.force_dispatch()
        self.assertIsNotNone(result)
        self.assertIsNone(dispatcher.pending_comic)
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0]["issue_number"], 42)

    def test_mid_speech_release_only_when_the_silence_gate_is_off(self):
        for gate, expected in ((0, 1), (50, 0)):
            dispatched = []
            dispatcher = SneakyDispatcher(vad=AudioVAD(), on_dispatch=dispatched.append)
            dispatcher.vad.is_speaking = True

            async def run():
                dispatcher.queue_comic({"id": 1, "issue_number": 42, "title": "Gate"})
                dispatcher._running = True
                task = asyncio.create_task(dispatcher._monitor_loop())
                await asyncio.sleep(1.2)
                dispatcher.stop()
                task.cancel()

            with patch.object(settings, "SNEAKY_SILENCE_SECONDS", gate):
                asyncio.run(run())
            self.assertEqual(len(dispatched), expected, f"gate {gate}s")

if __name__ == "__main__":
    unittest.main()
