"""
Audio Transcriber with differential privacy word scrambling.
- TriggerListener: rolling few-second Whisper checks for trigger phrases, text dropped at once.
- Transcriber: speech buffered in memory, transcribed and scrambled in batches every few minutes.
Nothing is stored here: the scrambled text goes to the miner (see privacy/thematic_miner.py).
"""
import io
import re
import wave
import time
import asyncio
import logging
import threading
from typing import Optional, Callable, List, Tuple
from server.audio.vad import SpeechSegment
from server.privacy.pos_scrambler import scramble_text
from server.config import settings

logger = logging.getLogger("comack.transcriber")

# Global reference to faster_whisper model if loaded
_whisper_model = None
_model_failed = False
_model_lock = threading.Lock()  # trigger checks and batches load it from different threads


def _get_whisper_model():
    global _whisper_model, _model_failed
    with _model_lock:
        if _whisper_model is not None or _model_failed:
            return _whisper_model
        try:
            from faster_whisper import WhisperModel
            logger.info(f"Loading faster-whisper model: {settings.WHISPER_MODEL_SIZE} on {settings.WHISPER_DEVICE}")
            device = "cuda" if settings.WHISPER_DEVICE == "cuda" else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"
            # 2 workers: a trigger check never waits behind a multi-minute batch.
            _whisper_model = WhisperModel(settings.WHISPER_MODEL_SIZE, device=device,
                                          compute_type=compute_type, num_workers=2)
            logger.info("faster-whisper model loaded successfully.")
        except Exception as e:
            logger.warning(f"Could not load faster-whisper: {e}. Falling back to conversational mock/fallback transcriber.")
            _model_failed = True
            _whisper_model = None
        return _whisper_model


def pcm_to_wav_bytes(pcm_data: bytes, sample_rate: int = 16000) -> bytes:
    """Wrap raw 16-bit mono PCM into standard WAV bytes."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def find_trigger(text: str) -> Optional[str]:
    """Whisper punctuates freely ("Alpha-Protocol."), so compare on lowercase words only."""
    words = " " + " ".join(re.findall(r"[a-z0-9']+", text.lower())) + " "
    return next((p for p in settings.SECRET_PHRASES if f" {p} " in words), None)


class TriggerListener:
    """Transcribes the last few seconds of live audio every couple of seconds, only to spot
    trigger phrases. The window's text is checked and dropped at once; nothing is kept."""

    def __init__(self, transcribe: Callable[[bytes], str], on_trigger: Callable[[str], None],
                 sample_rate: int = 16000, window_s: float = 4.0, every_s: float = 2.0,
                 cooldown_s: float = 10.0):
        self._transcribe = transcribe
        self._on_trigger = on_trigger
        self._window = bytearray()
        self._max_bytes = int(window_s * sample_rate) * 2
        self._every_bytes = int(every_s * sample_rate) * 2
        self._cooldown_s = cooldown_s  # overlapping windows hear the phrase twice
        self._fresh = 0
        self._heard_speech = False
        self._busy = False
        self._quiet_until = 0.0

    def feed(self, pcm: bytes, is_speech: bool):
        """Per audio chunk, on the event loop. The check itself runs in a worker thread."""
        self._window += pcm
        del self._window[:-self._max_bytes]
        self._fresh += len(pcm)
        self._heard_speech |= is_speech
        if self._fresh < self._every_bytes or self._busy:
            return
        heard, self._fresh, self._heard_speech = self._heard_speech, 0, False
        if heard and time.time() >= self._quiet_until:
            self._busy = True
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, self._check, bytes(self._window), loop)

    def _check(self, pcm: bytes, loop: asyncio.AbstractEventLoop):
        try:
            phrase = find_trigger(self._transcribe(pcm))
            if phrase:
                self._quiet_until = time.time() + self._cooldown_s
                logger.warning("🚨 Secret trigger phrase detected in speech!")
                loop.call_soon_threadsafe(self._on_trigger, phrase)
        except Exception as e:
            logger.error(f"Trigger check failed: {e}")
        finally:
            self._busy = False


class Transcriber:
    """Speech is buffered in memory and transcribed in one batch every few minutes."""

    def __init__(self):
        self._pcm: List[bytes] = []
        self._start: Optional[float] = None

    def add_segment(self, segment: SpeechSegment):
        if self._start is None:
            self._start = segment.timestamp
        self._pcm.append(segment.pcm_bytes)

    def take(self) -> Optional[Tuple[bytes, float]]:
        """Hand over (and forget) all speech heard since the last take. Call on the event loop."""
        pcm, start = b"".join(self._pcm), self._start
        self._pcm, self._start = [], None
        return (pcm, start) if pcm else None

    def transcribe_batch(self, pcm: bytes, start: float) -> Optional[Tuple[str, float, float]]:
        """Blocking. Returns (scrambled text, start timestamp, seconds of speech)."""
        duration = len(pcm) / 32000.0
        raw_text = self._transcribe(pcm)
        del pcm
        if not raw_text or not raw_text.strip():
            return None
        # First scramble pass. This scrambled transcript lives in memory only and is NEVER
        # stored; the miner turns it into the rows that are.
        scrambled = scramble_text(raw_text.strip(), settings.POS_SCRAMBLE_RATE)
        del raw_text
        return scrambled, start, duration

    def _transcribe(self, pcm_bytes: bytes) -> str:
        """Internal transcription dispatcher."""
        model = _get_whisper_model()
        if model is not None:
            try:
                wav_io = io.BytesIO(pcm_to_wav_bytes(pcm_bytes))
                segments, _ = model.transcribe(wav_io, beam_size=2, language="en")
                text_parts = [s.text.strip() for s in segments]
                return " ".join(text_parts)
            except Exception as e:
                logger.error(f"Error in faster-whisper transcription: {e}")
                
        # High quality conversational mock fallback for testing without GPU/faster-whisper
        return self._generate_fallback_speech(len(pcm_bytes) / 32000.0)

    def _generate_fallback_speech(self, duration_seconds: float) -> str:
        """Fallback phrase generator proportional to speech duration."""
        import random
        short_phrases = [
            "Wait, did you check the calendar?",
            "I thought the meeting was at three.",
            "Can you pass the mug?",
            "Look at what happened to the server.",
            "I'm pretty sure we left the lights on.",
            "What did they say about the deadline?"
        ]
        medium_phrases = [
            "The coffee machine is making that weird sound again, maybe we should unplug it.",
            "I was looking at the metrics and the CPU usage spiked right around noon.",
            "We definitely need to order more groceries before tomorrow morning.",
            "If the package arrives, just leave it on the front porch table."
        ]
        long_phrases = [
            "I was talking to the team earlier and they mentioned the entire schedule is getting pushed back by two weeks because of that unexpected supply chain issue.",
            "Remember that strange noise outside last night? It turns out the neighbor's raccoon discovered the compost bin again."
        ]
        
        if duration_seconds < 2.0:
            return random.choice(short_phrases)
        elif duration_seconds < 5.0:
            return random.choice(medium_phrases)
        else:
            return random.choice(long_phrases)
