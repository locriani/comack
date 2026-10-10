"""
Real-time Voice Activity Detection (VAD) and Silence Monitor for Comack.
Analyzes 16kHz 16-bit mono PCM audio chunks, detects speech intervals,
tracks silence duration for 'Sneaky Comic Mode', and packages speech segments.
"""
import time
import math
import struct
try:
    import numpy as np
except ImportError:
    np = None
from typing import Optional, Callable, List, Tuple
from dataclasses import dataclass


@dataclass
class SpeechSegment:
    pcm_bytes: bytes
    timestamp: float
    duration_seconds: float
    device_id: Optional[str] = None  # which device's pipeline heard this; None = unattributed (a bare VAD)


class AudioVAD:
    def __init__(self,
                 sample_rate: int = 16000,
                 energy_threshold: float = 650.0,
                 speech_pad_seconds: float = 0.8,
                 on_speech_segment: Optional[Callable[[SpeechSegment], None]] = None,
                 max_segment_seconds: float = 10.0,
                 device_id: Optional[str] = None):
        """
        :param sample_rate: Sample rate in Hz (default 16000)
        :param energy_threshold: Minimum RMS energy for speech (approx 500-1500 for 16-bit PCM)
        :param speech_pad_seconds: Trailing silence duration before finalizing a speech segment
        :param on_speech_segment: Callback when a speech segment is finalized
        :param device_id: Stamped onto every emitted segment, so each room's speech is
            attributable downstream. One VAD per device: the adaptive noise floor and
            the silence clock below must learn a single room, never two at once.
        """
        self.sample_rate = sample_rate
        self.energy_threshold = energy_threshold
        self.speech_pad_seconds = speech_pad_seconds
        self.on_speech_segment = on_speech_segment
        self.max_segment_seconds = max_segment_seconds
        self.device_id = device_id
        
        # Live state
        self.is_speaking = False
        self.last_speech_timestamp = time.time()
        self.last_chunk_timestamp = time.time()

        # Audio accumulator
        self._speech_buffer: List[bytes] = []
        self._segment_start_time: Optional[float] = None
        self._consecutive_silent_frames = 0
        self._consecutive_speech_frames = 0
        self._ambient_noise = 200.0

    @property
    def current_silence_seconds(self) -> float:
        if self.is_speaking:
            return 0.0
        return max(0.0, time.time() - self.last_speech_timestamp)

    def compute_rms(self, pcm_bytes: bytes) -> float:
        """Compute Root Mean Square (RMS) energy of 16-bit signed PCM bytes."""
        if len(pcm_bytes) < 2:
            return 0.0
        if np is not None:
            # Convert to numpy array of int16
            samples = np.frombuffer(pcm_bytes, dtype=np.int16)
            if len(samples) == 0:
                return 0.0
            mean_sq = np.mean(samples.astype(np.float64) ** 2)
            return float(math.sqrt(mean_sq))
        else:
            num_samples = len(pcm_bytes) // 2
            if num_samples == 0:
                return 0.0
            samples = struct.unpack(f"<{num_samples}h", pcm_bytes)
            sum_sq = sum(s * s for s in samples)
            return float(math.sqrt(sum_sq / num_samples))

    def process_chunk(self, pcm_bytes: bytes) -> Tuple[bool, float]:
        """
        Process a chunk of 16-bit mono PCM.
        Returns: (is_chunk_speech: bool, current_silence_duration: float)
        """
        now = time.time()
        chunk_duration = (len(pcm_bytes) / 2) / self.sample_rate
        rms = self.compute_rms(pcm_bytes)
        
        # Dynamic noise floor tracking
        if rms < self.energy_threshold:
            self._ambient_noise = 0.95 * self._ambient_noise + 0.05 * rms
            
        dynamic_threshold = max(self.energy_threshold, self._ambient_noise * 2.2)
        chunk_is_speech = rms > dynamic_threshold
        
        if chunk_is_speech:
            self._consecutive_speech_frames += 1
            self._consecutive_silent_frames = 0
            self.last_speech_timestamp = now
            
            # Start speech segment if not already
            if not self.is_speaking and self._consecutive_speech_frames >= 2:
                self.is_speaking = True
                self._segment_start_time = now
                self._speech_buffer = []
                
            if self.is_speaking:
                self._speech_buffer.append(pcm_bytes)
                # Continuous sound (TV, music) never yields the trailing pause, so
                # flush long segments to keep transcription (and trigger phrases) flowing.
                # ponytail: a phrase straddling the cut is missed; overlap chunks if that bites.
                if len(self._speech_buffer) * chunk_duration >= self.max_segment_seconds:
                    self._emit_segment(now)

        else:
            self._consecutive_silent_frames += 1
            self._consecutive_speech_frames = 0
            
            if self.is_speaking:
                self._speech_buffer.append(pcm_bytes)
                silent_duration = self._consecutive_silent_frames * chunk_duration
                
                # Check if speech segment has ended
                if silent_duration >= self.speech_pad_seconds:
                    self.is_speaking = False
                    self._emit_segment(now)
                    self._segment_start_time = None

        self.last_chunk_timestamp = now
        return chunk_is_speech, self.current_silence_seconds

    def _emit_segment(self, now: float):
        total_bytes = b"".join(self._speech_buffer)
        total_duration = (len(total_bytes) / 2) / self.sample_rate

        # Only emit if segment has at least 0.4s of actual speech
        if total_duration >= 0.4 and self.on_speech_segment:
            self.on_speech_segment(SpeechSegment(
                pcm_bytes=total_bytes,
                timestamp=self._segment_start_time or (now - total_duration),
                duration_seconds=total_duration,
                device_id=self.device_id
            ))
        self._speech_buffer = []
        self._segment_start_time = now

    def reset_silence(self):
        """Manually reset the silence timer (e.g. when voice is detected externally)."""
        self.last_speech_timestamp = time.time()
