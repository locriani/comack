import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from server.audio.vad import AudioVAD, SpeechSegment
from server.audio.transcriber import Transcriber, TriggerListener, find_trigger
from server.config import settings

CHUNK = b"\x00" * 3200  # 100ms of 16kHz 16-bit audio


class TestSecretTrigger(unittest.TestCase):
    def test_find_trigger_ignores_punctuation(self):
        self.assertEqual(find_trigger("Attention team, initiate Alpha-Protocol now."), "alpha protocol")
        self.assertIsNone(find_trigger("Can you pass the coffee beans please?"))
        with patch.object(settings, "SECRET_PHRASES", ["open sesame", "blue moon"]):
            self.assertEqual(find_trigger("Okay... Blue-Moon!"), "blue moon")
            self.assertIsNone(find_trigger("the moonlight is blue"))

    def test_listener_checks_rolling_window_and_fires_once(self):
        fired, windows = [], []

        def transcribe(pcm):
            windows.append(len(pcm))
            return "initiate alpha protocol"

        async def run():
            listener = TriggerListener(transcribe, fired.append, every_s=1.0, window_s=2.0)
            for _ in range(60):  # 6s of speech
                listener.feed(CHUNK, True)
                await asyncio.sleep(0.01)  # let worker checks finish
            await asyncio.sleep(0.1)

        asyncio.run(run())
        self.assertEqual(fired, ["alpha protocol"])     # cooldown: overlapping windows fire once
        self.assertEqual(windows, [32000])              # 1st check at 1s saw a 1s window

    def test_listener_skips_silence(self):
        calls = []

        async def run():
            listener = TriggerListener(lambda pcm: calls.append(pcm) or "", lambda p: None, every_s=1.0)
            for _ in range(30):
                listener.feed(CHUNK, False)

        asyncio.run(run())
        self.assertEqual(calls, [])

    def test_speech_is_batched_then_scrambled(self):
        t = Transcriber()
        t.add_segment(SpeechSegment(pcm_bytes=CHUNK, timestamp=5.0, duration_seconds=0.1))
        t.add_segment(SpeechSegment(pcm_bytes=CHUNK, timestamp=9.0, duration_seconds=0.1))
        pcm, start = t.take()
        self.assertEqual((len(pcm), start), (6400, 5.0))
        self.assertIsNone(t.take())
        t._transcribe = lambda pcm: "Call me at 555-1234."
        text, start, dur = t.transcribe_batch(pcm, start)
        self.assertNotIn("555-1234", text)
        self.assertAlmostEqual(dur, 0.2)

    def test_unreliable_segments_are_skipped(self):
        def seg(text, logprob=-0.3, ratio=1.5, no_speech=0.1):
            return SimpleNamespace(text=f" {text}", avg_logprob=logprob, compression_ratio=ratio,
                                   no_speech_prob=no_speech)

        class Model:
            def transcribe(self, *a, **k):
                return iter([seg("Pass the mug."), seg("mumble", logprob=-1.4),
                             seg("thanks thanks thanks", ratio=3.1),
                             seg("Thank you for watching.", no_speech=0.9), seg("Sure.")]), None

        with patch("server.audio.transcriber._get_whisper_model", return_value=Model()):
            self.assertEqual(Transcriber()._transcribe(CHUNK), "Pass the mug. Sure.")

        class Broken:
            def transcribe(self, *a, **k):
                raise RuntimeError("cuda died")

        with patch("server.audio.transcriber._get_whisper_model", return_value=Broken()):
            self.assertEqual(Transcriber()._transcribe(CHUNK), "")   # skipped, not mock speech

    def test_continuous_sound_still_yields_segments(self):
        segments = []
        vad = AudioVAD(on_speech_segment=segments.append, max_segment_seconds=2.0)
        loud = (b"\xff\x3f" * 1600)  # 100ms of constant ~16k amplitude, never silent
        for _ in range(50):  # 5s
            vad.process_chunk(loud)
        self.assertGreaterEqual(len(segments), 2)


if __name__ == "__main__":
    unittest.main()
