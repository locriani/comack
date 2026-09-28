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

    def test_configured_phrases_ignore_punctuation(self):
        from server.config import settings
        triggered = []
        transcriber = Transcriber(on_secret_trigger=triggered.append)
        old = settings.SECRET_PHRASES
        settings.SECRET_PHRASES = ["open sesame", "blue moon"]
        try:
            for text in ("Okay... Blue-Moon!", "open sesame", "the moonlight is blue"):
                transcriber._transcribe = lambda pcm, t=text: t
                transcriber.process_segment(SpeechSegment(timestamp=1.0, duration_seconds=1.0, pcm_bytes=b"\x00" * 3200))
        finally:
            settings.SECRET_PHRASES = old
        self.assertEqual(triggered, ["blue moon", "open sesame"])

    def test_continuous_sound_still_yields_segments(self):
        from server.audio.vad import AudioVAD
        segments = []
        vad = AudioVAD(on_speech_segment=segments.append, max_segment_seconds=2.0)
        loud = (b"\xff\x3f" * 1600)  # 100ms of constant ~16k amplitude, never silent
        for _ in range(50):  # 5s
            vad.process_chunk(loud)
        self.assertGreaterEqual(len(segments), 2)

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
