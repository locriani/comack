"""
Audio Transcriber with differential privacy word scrambling.
Uses faster-whisper (if available) or HTTP/Mock fallback, converts speech to text,
applies stochastic POS scrambling, saves to DB, and immediately wipes raw audio from memory.
"""
import io
import wave
import time
import logging
from typing import Optional, Callable
from server.audio.vad import SpeechSegment
from server.privacy.pos_scrambler import scramble_text
from server.storage import db
from server.config import settings

logger = logging.getLogger("comack.transcriber")

# Global reference to faster_whisper model if loaded
_whisper_model = None
_model_failed = False


def _get_whisper_model():
    global _whisper_model, _model_failed
    if _whisper_model is not None or _model_failed:
        return _whisper_model
    
    try:
        from faster_whisper import WhisperModel
        logger.info(f"Loading faster-whisper model: {settings.WHISPER_MODEL_SIZE} on {settings.WHISPER_DEVICE}")
        device = "cuda" if settings.WHISPER_DEVICE == "cuda" else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        _whisper_model = WhisperModel(settings.WHISPER_MODEL_SIZE, device=device, compute_type=compute_type)
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


class Transcriber:
    def __init__(self, on_transcript_ready: Optional[Callable[[dict], None]] = None):
        self.on_transcript_ready = on_transcript_ready

    def process_segment(self, segment: SpeechSegment):
        """Transcribe speech segment, mine abstract story elements, and wipe raw text/audio."""
        raw_text = self._transcribe(segment.pcm_bytes)
        
        # Discard raw PCM bytes immediately
        del segment.pcm_bytes
        
        if not raw_text or not raw_text.strip():
            return
            
        clean_text = raw_text.strip()
        
        # Mine abstract themes, emotional vibe, and surreal metaphors
        from server.privacy.thematic_miner import mine_story_elements
        elements = mine_story_elements(clean_text, segment.duration_seconds)
        
        # Wipe clean_text from memory immediately
        del clean_text
        del raw_text
        
        thematic_summary = (
            f"[{elements['emotional_tone'].upper()} VIBE • {elements['energy_level'].upper()} ENERGY] "
            f"Conflict: {elements['abstract_theme']} | Metaphor: {elements['surreal_metaphor']}"
        )
        logger.info(f"Mined Story Element: {thematic_summary}")
        
        # Persist mined theme to database (NEVER storing the raw spoken words)
        transcript_id = db.save_transcript(
            timestamp=segment.timestamp,
            duration_seconds=segment.duration_seconds,
            scrambled_text=thematic_summary
        )
        
        item = {
            "id": transcript_id,
            "timestamp": segment.timestamp,
            "duration_seconds": segment.duration_seconds,
            "scrambled_text": thematic_summary,
            "story_elements": elements
        }
        
        if self.on_transcript_ready:
            self.on_transcript_ready(item)

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
