import unittest
from server.audio.vad import SpeechSegment
from server.audio.transcriber import Transcriber

class TestSecretTrigger(unittest.TestCase):
    def test_alpha_protocol_detected(self):
        triggered_phrases = []
        
        def mock_transcribe(pcm_bytes):
            return "Attention team, initiate alpha protocol now please."
            
        transcriber = Transcriber(
            on_secret_trigger=lambda phrase: triggered_phrases.append(phrase)
        )
        transcriber._transcribe = mock_transcribe
        
        seg = SpeechSegment(
            timestamp=123456789.0,
            duration_seconds=2.5,
            pcm_bytes=b"\x00" * 3200
        )
        
        transcriber.process_segment(seg)
        self.assertEqual(len(triggered_phrases), 1)
        self.assertEqual(triggered_phrases[0], "alpha protocol")

    def test_normal_speech_no_trigger(self):
        triggered_phrases = []
        
        def mock_transcribe(pcm_bytes):
            return "Can you pass the coffee beans please?"
            
        transcriber = Transcriber(
            on_secret_trigger=lambda phrase: triggered_phrases.append(phrase)
        )
        transcriber._transcribe = mock_transcribe
        
        seg = SpeechSegment(
            timestamp=123456789.0,
            duration_seconds=1.5,
            pcm_bytes=b"\x00" * 3200
        )
        
        transcriber.process_segment(seg)
        self.assertEqual(len(triggered_phrases), 0)

if __name__ == "__main__":
    unittest.main()
