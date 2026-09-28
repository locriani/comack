"""
Sneaky Comic Mode Dispatcher.
Enforces strict room silence (45-60s) before authorizing the reTerminal E1001
to silently refresh its e-Paper screen.
"""
import time
import asyncio
import logging
from typing import Optional, Dict, Any, Callable, List
from server.config import settings
from server.storage import db
from server.audio.vad import AudioVAD

logger = logging.getLogger("comack.sneaky")


class SneakyDispatcher:
    def __init__(self, vad: AudioVAD, on_dispatch: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.vad = vad
        self.on_dispatch = on_dispatch
        self.pending_comic: Optional[Dict[str, Any]] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        """Start the background silence monitoring loop."""
        if not self._running:
            self._running = True
            # Load any existing stealth hold comic from database
            held = db.get_stealth_hold_comic()
            if held:
                self.pending_comic = held
                logger.info(f"Loaded pending stealth comic #{held['issue_number']} from database.")
            self._task = asyncio.create_task(self._monitor_loop())

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    def queue_comic(self, comic_dict: Dict[str, Any]):
        """Place a newly generated comic into the stealth queue."""
        self.pending_comic = comic_dict
        logger.info(
            f"Comic #{comic_dict['issue_number']} placed in STEALTH HOLD. "
            f"Waiting for {settings.SNEAKY_SILENCE_SECONDS:.0f}s of continuous silence."
        )

    def force_dispatch(self) -> Optional[Dict[str, Any]]:
        """Bypass the silence requirement and release immediately (for testing)."""
        comic = self.pending_comic or db.get_stealth_hold_comic()
        if comic:
            self._execute_dispatch(comic, forced=True)
            return comic
        return None

    def get_status(self) -> Dict[str, Any]:
        """Return current status of sneaky mode and room audio."""
        silence_sec = self.vad.current_silence_seconds
        required_sec = settings.SNEAKY_SILENCE_SECONDS
        pct = min(100.0, (silence_sec / required_sec) * 100.0) if required_sec > 0 else 100.0
        
        return {
            "has_stealth_comic": self.pending_comic is not None,
            "pending_issue_number": self.pending_comic["issue_number"] if self.pending_comic else None,
            "pending_series_issue": self.pending_comic.get("series_issue") if self.pending_comic else None,
            "is_speaking": self.vad.is_speaking,
            "current_silence_seconds": round(silence_sec, 1),
            "required_silence_seconds": required_sec,
            "silence_progress_pct": round(pct, 1),
            "ready_to_reveal": (self.pending_comic is not None and silence_sec >= required_sec and not self.vad.is_speaking)
        }

    async def _monitor_loop(self):
        """Continuously monitors silence duration while a comic is queued."""
        while self._running:
            try:
                await asyncio.sleep(1.0)
                
                if not self.pending_comic:
                    # Check if DB has one
                    held = db.get_stealth_hold_comic()
                    if held:
                        self.pending_comic = held
                    else:
                        continue
                        
                silence_duration = self.vad.current_silence_seconds
                is_speaking = self.vad.is_speaking
                
                if is_speaking:
                    # Voice detected: screen must not update!
                    continue
                    
                # Check if strict silence threshold is satisfied
                if silence_duration >= settings.SNEAKY_SILENCE_SECONDS:
                    logger.info(
                        f"STRICT SILENCE DETECTED ({silence_duration:.1f}s >= {settings.SNEAKY_SILENCE_SECONDS:.0f}s). "
                        f"Executing sneaky release of Comic #{self.pending_comic['issue_number']}!"
                    )
                    comic = self.pending_comic
                    self._execute_dispatch(comic, forced=False)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in sneaky monitor loop: {e}")

    def _execute_dispatch(self, comic: Dict[str, Any], forced: bool = False):
        """Mark comic displayed in DB and notify connected devices."""
        silence_sec = self.vad.current_silence_seconds
        db.mark_comic_displayed(comic["id"], silence_seconds=silence_sec)
        self.pending_comic = None
        
        payload = {
            "event": "SNEAKY_COMIC_REFRESH",
            "issue_number": comic["issue_number"],
            "series_issue": comic.get("series_issue"),
            "title": comic["title"],
            "silence_seconds": round(silence_sec, 1),
            "forced": forced,
            "bin_url": "/api/comics/current.bin",
            "png_url": "/api/comics/current.png",
            "timestamp": time.time()
        }
        
        if self.on_dispatch:
            self.on_dispatch(payload)
