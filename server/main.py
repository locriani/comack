"""
Comack Main Server.
FastAPI application with WebSocket audio streaming, real-time VAD,
30-minute comic cycle scheduler, sneaky silence dispatcher, and REST API.
"""
import time
import json
import asyncio
import logging
from pathlib import Path
from typing import Set, Dict, Any, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from contextlib import asynccontextmanager

from server.config import settings, COMICS_DIR, STATIC_DIR
from server.storage import db
from server.audio.vad import AudioVAD, SpeechSegment
from server.audio.transcriber import Transcriber
from server.story.narrative_engine import narrative_engine
from server.render.comic_compositor import comic_compositor
from server.dispatcher.sneaky_dispatcher import SneakyDispatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("comack.main")

# Active WebSocket connections
event_subscribers: Set[WebSocket] = set()

# Global instances
transcriber: Transcriber = None
vad: AudioVAD = None
sneaky_dispatcher: SneakyDispatcher = None
cycle_task: asyncio.Task = None


def broadcast_event(event_data: Dict[str, Any]):
    """Broadcast an event to all connected web UI and hardware clients."""
    message = json.dumps(event_data)
    dead_sockets = set()
    for ws in list(event_subscribers):
        try:
            asyncio.create_task(ws.send_text(message))
        except Exception:
            dead_sockets.add(ws)
    event_subscribers.difference_update(dead_sockets)


async def run_comic_generation_cycle() -> Dict[str, Any]:
    """Execute a 30-minute comic generation cycle."""
    logger.info("Executing comic generation cycle...")
    
    # 1. Fetch unconsumed scrambled transcripts
    unprocessed = db.get_unprocessed_transcripts()
    logger.info(f"Gathered {len(unprocessed)} dialogue snippets for this cycle.")
    
    # 2. Generate script with narrative continuity
    script = await narrative_engine.generate_comic_script(unprocessed)
    
    # 3. Render and dither 800x480 comic image + 48,000-byte bin
    png_path, bin_path = comic_compositor.compose_comic(script)
    
    # 4. Save comic into database
    comic_id = db.save_comic(
        issue_number=script["issue_number"],
        arc_id=script["arc_id"],
        title=script["title"],
        layout_type=script["layout_type"],
        panel_count=script["panel_count"],
        script_json=script,
        image_path=str(png_path),
        bin_path=str(bin_path)
    )
    
    # 5. Mark transcripts as used
    if unprocessed:
        transcript_ids = [t["id"] for t in unprocessed]
        db.mark_transcripts_used(transcript_ids, comic_id)
        
    comic_record = {
        "id": comic_id,
        "issue_number": script["issue_number"],
        "title": script["title"],
        "layout_type": script["layout_type"],
        "panel_count": script["panel_count"],
        "created_at": time.time(),
        "status": "STEALTH_HOLD",
        "script": script,
        "png_url": f"/api/comics/{script['issue_number']}/png",
        "bin_url": f"/api/comics/{script['issue_number']}/bin"
    }
    
    # 6. Queue comic in Sneaky Mode
    sneaky_dispatcher.queue_comic(comic_record)
    
    # Broadcast to clients
    broadcast_event({
        "event": "NEW_COMIC_QUEUED",
        "comic": comic_record,
        "status": sneaky_dispatcher.get_status()
    })
    
    return comic_record


async def periodic_comic_scheduler():
    """Background task running every COMIC_CYCLE_MINUTES."""
    interval_seconds = settings.COMIC_CYCLE_MINUTES * 60
    logger.info(f"Comic cycle scheduler started. Interval: {settings.COMIC_CYCLE_MINUTES} minutes.")
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await run_comic_generation_cycle()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic comic scheduler: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global transcriber, vad, sneaky_dispatcher, cycle_task
    
    # 1. Initialize components
    transcriber = Transcriber(on_transcript_ready=lambda t: broadcast_event({
        "event": "NEW_TRANSCRIPT", "transcript": t
    }))
    
    vad = AudioVAD(
        sample_rate=16000,
        energy_threshold=650.0,
        speech_pad_seconds=0.8,
        on_speech_segment=lambda seg: transcriber.process_segment(seg)
    )
    
    sneaky_dispatcher = SneakyDispatcher(
        vad=vad,
        on_dispatch=lambda p: broadcast_event(p)
    )
    sneaky_dispatcher.start()
    
    # Ensure at least one initial comic exists on startup so screen is never blank
    if not db.get_current_displayed_comic() and not db.get_stealth_hold_comic():
        logger.info("No existing comic found. Generating initial comic Issue #001...")
        initial_comic = await run_comic_generation_cycle()
        sneaky_dispatcher.force_dispatch()
        
    # Start periodic 30-min timer
    cycle_task = asyncio.create_task(periodic_comic_scheduler())
    
    yield
    
    # Teardown
    sneaky_dispatcher.stop()
    if cycle_task:
        cycle_task.cancel()


app = FastAPI(title="Comack", lifespan=lifespan)


# --- Static and Web UI ---
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
async def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h1>Comack is running</h1><p>Static UI not yet written.</p>")


# --- WebSocket Endpoints ---

@app.websocket("/ws/audio")
async def ws_audio_stream(websocket: WebSocket):
    """
    Receives continuous 16kHz 16-bit mono PCM stream from reTerminal E1001 or simulator.
    """
    await websocket.accept()
    logger.info("reTerminal E1001 audio streamer connected.")
    try:
        while True:
            data = await websocket.receive_bytes()
            if data:
                is_speech, silence_duration = vad.process_chunk(data)
    except WebSocketDisconnect:
        logger.info("reTerminal audio stream disconnected.")
    except Exception as e:
        logger.warning(f"Audio stream error: {e}")


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    """
    Subscribes reTerminal E1001 and Web UI to status updates and sneaky refresh events.
    """
    await websocket.accept()
    event_subscribers.add(websocket)
    
    # Send initial snapshot
    await websocket.send_text(json.dumps({
        "event": "INIT_SNAPSHOT",
        "sneaky_status": sneaky_dispatcher.get_status(),
        "current_comic": db.get_current_displayed_comic(),
        "active_arc": db.get_active_arc()
    }))
    
    try:
        while True:
            # Handle client heartbeats or incoming commands
            msg = await websocket.receive_text()
            data = json.loads(msg)
            if data.get("cmd") == "PING":
                await websocket.send_text(json.dumps({
                    "event": "PONG",
                    "sneaky_status": sneaky_dispatcher.get_status()
                }))
    except WebSocketDisconnect:
        event_subscribers.discard(websocket)
    except Exception:
        event_subscribers.discard(websocket)


# --- REST Endpoints ---

@app.get("/api/status")
async def get_system_status():
    arc = db.get_active_arc()
    current = db.get_current_displayed_comic()
    sneaky = sneaky_dispatcher.get_status()
    
    return {
        "status": "online",
        "timestamp": time.time(),
        "active_arc": arc,
        "current_comic": current,
        "sneaky": sneaky,
        "settings": {
            "silence_threshold_seconds": settings.SNEAKY_SILENCE_SECONDS,
            "cycle_minutes": settings.COMIC_CYCLE_MINUTES,
            "pos_scramble_rate": settings.POS_SCRAMBLE_RATE
        }
    }


@app.get("/api/comics/current")
async def get_current_comic():
    comic = db.get_current_displayed_comic()
    if not comic:
        raise HTTPException(status_code=404, detail="No displayed comic yet")
    return comic


@app.get("/api/comics/current.png")
async def get_current_comic_png():
    path = COMICS_DIR / "current.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Current comic PNG not found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/comics/current.bin")
async def get_current_comic_bin():
    path = COMICS_DIR / "current.bin"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Current comic BIN not found")
    with open(path, "rb") as f:
        data = f.read()
    return Response(content=data, media_type="application/octet-stream")


@app.get("/api/comics/history")
async def get_comic_history(limit: int = 20):
    return db.get_recent_comics(limit=limit)


@app.get("/api/comics/{issue_number}/png")
async def get_issue_png(issue_number: int):
    path = COMICS_DIR / f"issue_{issue_number:04d}.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Issue #{issue_number} PNG not found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/comics/{issue_number}/bin")
async def get_issue_bin(issue_number: int):
    path = COMICS_DIR / f"issue_{issue_number:04d}.bin"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Issue #{issue_number} BIN not found")
    with open(path, "rb") as f:
        data = f.read()
    return Response(content=data, media_type="application/octet-stream")


@app.get("/api/transcripts/recent")
async def get_recent_transcripts():
    return db.get_unprocessed_transcripts()


@app.post("/api/comics/generate")
async def trigger_generate():
    """Manually trigger comic generation right now."""
    comic = await run_comic_generation_cycle()
    return {"status": "generated", "comic": comic}


@app.post("/api/comics/force_reveal")
async def trigger_force_reveal():
    """Bypass silence countdown and reveal pending comic immediately."""
    comic = sneaky_dispatcher.force_dispatch()
    if not comic:
        return {"status": "none_pending", "message": "No comic was waiting in stealth queue."}
    return {"status": "dispatched", "comic": comic}


@app.post("/api/test/simulate_speech")
async def simulate_speech_dialogue(payload: Dict[str, Any]):
    """Inject a test phrase into the pipeline for validation."""
    text = payload.get("text", "The quick brown fox jumps over the lazy dog.")
    duration = float(payload.get("duration", 2.5))
    
    # Reset silence
    vad.last_speech_timestamp = time.time()
    vad.current_silence_seconds = 0.0
    
    # Transcribe & scramble
    from server.privacy.pos_scrambler import scramble_text
    scrambled = scramble_text(text, settings.POS_SCRAMBLE_RATE)
    t_id = db.save_transcript(time.time(), duration, scrambled)
    
    item = {"id": t_id, "timestamp": time.time(), "duration_seconds": duration, "scrambled_text": scrambled}
    broadcast_event({"event": "NEW_TRANSCRIPT", "transcript": item})
    
    return {"status": "simulated", "transcript": item}
