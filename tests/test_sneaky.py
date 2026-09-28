import time
import unittest
from server.audio.vad import AudioVAD
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

if __name__ == "__main__":
    unittest.main()
