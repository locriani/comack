import time
import struct
import math
import unittest
from server.audio.vad import AudioVAD, SpeechSegment

def make_sine_chunk(freq=440, amp=15000, samples=1600):
    """Generate 1600 samples of 16-bit PCM."""
    data = []
    for i in range(samples):
        t = i / 16000.0
        val = int(amp * math.sin(2 * math.pi * freq * t))
        data.append(val)
    return struct.pack(f"<{samples}h", *data)

def make_silence_chunk(samples=1600):
    return b"\x00" * (samples * 2)

class TestAudioVAD(unittest.TestCase):
    def test_vad_speech_detection(self):
        detected_segments = []
        def on_seg(seg):
            detected_segments.append(seg)
            
        vad = AudioVAD(energy_threshold=500.0, speech_pad_seconds=0.3, on_speech_segment=on_seg)
        
        # 1. Send silence
        is_speech, sil_dur = vad.process_chunk(make_silence_chunk())
        self.assertFalse(is_speech)
        self.assertFalse(vad.is_speaking)
        
        # 2. Send loud speech chunks
        speech_chunk = make_sine_chunk(freq=300, amp=12000)
        for _ in range(5):
            is_speech, _ = vad.process_chunk(speech_chunk)
            self.assertTrue(is_speech)
            
        self.assertTrue(vad.is_speaking)
        self.assertEqual(vad.current_silence_seconds, 0.0)
        
        # 3. Send silence to finalize segment
        time.sleep(0.35)
        for _ in range(5):
            vad.process_chunk(make_silence_chunk())
            
        self.assertFalse(vad.is_speaking)
        self.assertEqual(len(detected_segments), 1)
        self.assertGreater(detected_segments[0].duration_seconds, 0.3)

if __name__ == "__main__":
    unittest.main()
