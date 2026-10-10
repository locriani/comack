"""
Per-device audio pipelines (spec: per-device audio).

One lane per device: its own AudioVAD (adaptive noise floor and silence clock),
its own TriggerListener window, its own transcript batch, and its own sneaky
dispatcher slot. The faster-whisper model is deliberately NOT one of these —
it stays a module-level singleton in server/audio/transcriber.py. Sharing the
model is right; the shared *buffer* was the single-device bug: a second
device's chunks interleaved into one VAD and one batch, merging two rooms'
speech into single segments and corrupting the noise threshold.

Pipelines are created on a device's first use and then idled — no teardown
machinery in v1. An idle pipeline costs one dormant object, and a device that
reconnects keeps its room's noise floor and silence clock.
"""
from typing import Any, Callable, Dict, List, Optional

from server.audio.transcriber import Transcriber, TriggerListener
from server.audio.vad import AudioVAD
from server.dispatcher.sneaky_dispatcher import SneakyDispatcher
from server.storage import db


class DevicePipeline:
    """One device's audio lane: the audio half of that device's identity.

    Created lazily by PipelineRegistry.for_device — never at import time — so
    construction may touch the database (restart recovery below) and may arm a
    dispatcher monitor, which needs a running event loop.
    """

    def __init__(self, device_id: str,
                 on_dispatch: Optional[Callable[[Dict[str, Any]], None]],
                 on_trigger: Callable[[str], None]):
        self.device_id = device_id
        self.connected = False  # a live /ws/audio stream is feeding this lane
        self.transcriber = Transcriber()  # own batch; the whisper model stays shared

        # Values mirror the single-device wiring this replaces (former main.lifespan).
        self.vad = AudioVAD(
            sample_rate=16000,
            energy_threshold=650.0,
            speech_pad_seconds=0.8,
            on_speech_segment=self.transcriber.add_segment,
            device_id=device_id,
        )
        self.trigger_listener = TriggerListener(self.transcriber._transcribe, on_trigger)
        self.dispatcher = SneakyDispatcher(vad=self.vad, device_id=device_id, on_dispatch=on_dispatch)

        # Restart recovery: a comic held in stealth when the server last stopped
        # re-enters THIS device's queue (and only this one — the query is
        # device-scoped), to wait on this room's silence again.
        held = db.get_stealth_hold_comic(device_id)
        if held:
            self.dispatcher.queue_comic(held)


class PipelineRegistry:
    """The per-device pipelines, created on first use and keyed by device id.

    The web layer's two side effects are injected by server.main at import
    time (on_dispatch, handle_trigger), keeping this module importable without
    the app — tests build pipelines against them directly.
    """

    def __init__(self) -> None:
        self._pipes: Dict[str, DevicePipeline] = {}
        # main.py sets these: sneaky releases broadcast to the event feed; a
        # heard phrase refreshes the hearing device's display.
        self.on_dispatch: Optional[Callable[[Dict[str, Any]], None]] = None
        self.handle_trigger: Optional[Callable[[str, str], None]] = None

    def for_device(self, device_id: str) -> DevicePipeline:
        """This device's pipeline, created on first use. Callers pass resolved
        device ids (server.devices.resolve_device); everything a pipeline
        produces — segments, transcripts, arcs, comics — inherits its id."""
        pipe = self._pipes.get(device_id)
        if pipe is None:
            pipe = self._pipes[device_id] = DevicePipeline(
                device_id,
                on_dispatch=self.on_dispatch,
                on_trigger=self._trigger_for(device_id),
            )
        return pipe

    def active(self) -> List[DevicePipeline]:
        """Pipelines with a live audio stream: the only ones a scheduler tick
        can make due (a room nobody streams from never asks for a comic)."""
        return [pipe for pipe in self._pipes.values() if pipe.connected]

    def all(self) -> List[DevicePipeline]:
        """Every known pipeline, connected or idled — a device's buffered speech
        still deserves transcription after it disconnects."""
        return list(self._pipes.values())

    def _trigger_for(self, device_id: str) -> Callable[[str], None]:
        def fire(phrase: str) -> None:
            if self.handle_trigger:
                self.handle_trigger(device_id, phrase)
        return fire


pipelines = PipelineRegistry()
