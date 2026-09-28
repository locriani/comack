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
from server.audio.transcriber import Transcriber, TriggerListener
from server.privacy.thematic_miner import mine_sentences
from server.privacy.pos_scrambler import pools
from server.story.narrative_engine import narrative_engine, prompt_log, expire_prompt_log
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
trigger_listener: TriggerListener = None


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


async def mine_and_store(scrambled: str, timestamp: float, duration: float):
    """Scrambled transcript -> LLM content per sentence -> second 25% swap -> the only thing stored."""
    for row in await mine_sentences(scrambled):
        logger.info(f"Mined Story Element: {row}")
        t_id = db.save_transcript(timestamp, duration, row)
        broadcast_event({"event": "NEW_TRANSCRIPT", "transcript": {
            "id": t_id, "timestamp": timestamp, "duration_seconds": duration, "scrambled_text": row}})


generation_lock = asyncio.Lock()  # one comic at a time: FLUX can't share the GPU, and issue numbers must not collide


async def run_comic_generation_cycle() -> Dict[str, Any]:
    async with generation_lock:
        return await _generation_cycle()


async def _generation_cycle() -> Dict[str, Any]:
    """Execute a 30-minute comic generation cycle."""
    logger.info("Executing comic generation cycle...")
    
    # 1. Fetch unconsumed scrambled transcripts
    unprocessed = db.get_unprocessed_transcripts()
    logger.info(f"Gathered {len(unprocessed)} dialogue snippets for this cycle.")
    
    # 2. Generate script with narrative continuity
    script = await narrative_engine.generate_comic_script(unprocessed)
    
    # 3. Render and dither 800x480 comic image + 48,000-byte bin
    # Off the event loop: FLUX waits for VRAM and renders for minutes; audio must keep flowing.
    png_path, bin_path = await asyncio.to_thread(comic_compositor.compose_comic, script)
    
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
    
    db.set_state("lines_wanted", str(script["lines_wanted_next"]))

    # 5. Mark transcripts as used
    if unprocessed:
        transcript_ids = [t["id"] for t in unprocessed]
        db.mark_transcripts_used(transcript_ids, comic_id)
        
    comic_record = {
        "id": comic_id,
        "issue_number": script["issue_number"],
        "series_issue": script["series_issue"],
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


_trigger_running = False


async def _trigger_cycle_and_dispatch():
    """Refresh the display now: reveal a held comic instantly, otherwise generate one and reveal it."""
    global _trigger_running
    if _trigger_running:  # the phrase often lands in several segments; one refresh is enough
        return
    _trigger_running = True
    try:
        async with generation_lock:  # a comic mid-generation is the one to reveal
            pass
        if sneaky_dispatcher.force_dispatch():
            logger.info("⚡ Held comic revealed immediately.")
            return
        logger.info("⚡ Nothing held; generating a comic for immediate display...")
        await run_comic_generation_cycle()
        sneaky_dispatcher.force_dispatch()
        logger.info("⚡ Immediate comic generation completed and dispatched to display!")
    except Exception as e:
        logger.error(f"Error executing immediate comic cycle: {e}")
    finally:
        _trigger_running = False


def comic_due(since_last_s: float, have_lines: int, wanted_lines: int) -> bool:
    """Generate once the requested room notes are in, bounded by the min/max gap between comics."""
    if since_last_s >= settings.COMIC_MAX_MINUTES * 60:
        return True
    return since_last_s >= settings.COMIC_MIN_MINUTES * 60 and have_lines >= wanted_lines


async def periodic_comic_scheduler():
    """Checks every 30s whether the next comic is due (see comic_due)."""
    started = time.time()
    logger.info(f"Comic scheduler started: {settings.COMIC_MIN_MINUTES}-{settings.COMIC_MAX_MINUTES} min between comics.")
    while True:
        try:
            await asyncio.sleep(30)
            last = db.get_recent_comics(limit=1)
            since = time.time() - (last[0]["created_at"] if last else started)
            wanted = int(db.get_state("lines_wanted", str(settings.DEFAULT_LINES_WANTED)))
            if not generation_lock.locked() and comic_due(since, len(db.get_unprocessed_transcripts()), wanted):
                await run_comic_generation_cycle()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic comic scheduler: {e}")


async def periodic_prompt_log_expiry():
    """Debug prompt log entries are gone within 30s of turning 15 minutes old."""
    while True:
        await asyncio.sleep(30)
        expire_prompt_log()


async def periodic_transcription():
    """Every PROCESS_INTERVAL_MINUTES: transcribe the buffered speech, scramble, mine and store."""
    while True:
        try:
            await asyncio.sleep(settings.PROCESS_INTERVAL_MINUTES * 60)
            batch = transcriber.take()
            if batch:
                result = await asyncio.to_thread(transcriber.transcribe_batch, *batch)
                del batch
                if result:
                    await mine_and_store(*result)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic transcription: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global transcriber, trigger_listener, vad, sneaky_dispatcher, cycle_task
    
    # 1. Initialize components
    loop = asyncio.get_running_loop()

    def handle_secret_trigger(phrase: str):
        logger.warning(f"🚨 [SECRET TRIGGER ACTIVATED] '{phrase.upper()}' detected in speech! Generating new frame immediately.")
        broadcast_event({
            "event": "SECRET_TRIGGER_ACTIVATED",
            "phrase": phrase,
            "message": f"Secret phrase '{phrase}' recognized! Generating new comic frame immediately."
        })
        loop.create_task(_trigger_cycle_and_dispatch())

    transcriber = Transcriber()
    trigger_listener = TriggerListener(transcriber._transcribe, handle_secret_trigger)

    vad = AudioVAD(
        sample_rate=16000,
        energy_threshold=650.0,
        speech_pad_seconds=0.8,
        on_speech_segment=transcriber.add_segment
    )
    
    sneaky_dispatcher = SneakyDispatcher(
        vad=vad,
        on_dispatch=lambda p: broadcast_event(p)
    )
    sneaky_dispatcher.start()
    
    # Ensure at least one initial comic exists so the screen is never blank; in the background,
    # so the API serves while it renders
    if not db.get_current_displayed_comic() and not db.get_stealth_hold_comic():
        logger.info("No existing comic found. Generating initial comic Issue #001...")
        asyncio.create_task(_trigger_cycle_and_dispatch())

    cycle_task = asyncio.create_task(periodic_comic_scheduler())
    transcribe_task = asyncio.create_task(periodic_transcription())
    expiry_task = asyncio.create_task(periodic_prompt_log_expiry())
    asyncio.create_task(asyncio.to_thread(pools))  # build scrambler word pools off the event loop
    
    yield
    
    # Teardown
    sneaky_dispatcher.stop()
    transcribe_task.cancel()  # ponytail: up to one interval of unprocessed speech is dropped on restart
    expiry_task.cancel()
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


@app.get("/reader", response_class=HTMLResponse)
async def reader():
    return FileResponse(STATIC_DIR / "reader.html")


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
                trigger_listener.feed(data, is_speech)
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
            "lines_wanted": int(db.get_state("lines_wanted", str(settings.DEFAULT_LINES_WANTED))),
            "lines_collected": len(db.get_unprocessed_transcripts()),
            "pos_scramble_rate": settings.POS_SCRAMBLE_RATE
        }
    }


@app.post("/api/trigger")
async def trigger_secret_phrase(payload: dict = None):
    """Manually invoke secret trigger (e.g. 'alpha protocol') to generate a new frame immediately."""
    phrase = (payload or {}).get("phrase", "alpha protocol")
    logger.warning(f"🚨 Manual trigger invoked: '{phrase}'")
    broadcast_event({
        "event": "SECRET_TRIGGER_ACTIVATED",
        "phrase": phrase,
        "message": f"Secret trigger '{phrase}' activated. Generating immediate comic frame!"
    })
    asyncio.create_task(_trigger_cycle_and_dispatch())
    return {"status": "ok", "message": f"Trigger '{phrase}' activated. Frame generation started."}


@app.get("/api/comics/current")
async def get_current_comic():
    comic = db.get_current_displayed_comic()
    if not comic:
        raise HTTPException(status_code=404, detail="No displayed comic yet")
    return comic


def _displayed_issue() -> int:
    comic = db.get_current_displayed_comic()
    if not comic:
        raise HTTPException(status_code=404, detail="No displayed comic yet")
    return comic["issue_number"]


@app.get("/api/comics/current.png")
async def get_current_comic_png():
    return await get_issue_png(_displayed_issue())


@app.get("/api/comics/current.bin")
async def get_current_comic_bin():
    return await get_issue_bin(_displayed_issue())


@app.get("/api/comics/history")
async def get_comic_history(limit: int = 20):
    return db.get_recent_comics(limit=limit)


@app.get("/api/arcs")
async def get_arcs():
    return db.get_arcs()


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


@app.get("/api/prompts")
async def get_prompts():
    """LLM and image prompts from the last 15 minutes with their outputs, newest first (memory only)."""
    expire_prompt_log()
    return list(reversed(prompt_log))


@app.post("/api/comics/generate")
async def trigger_generate():
    """Manually trigger comic generation right now."""
    comic = await run_comic_generation_cycle()
    return {"status": "generated", "comic": comic}


@app.post("/api/comics/{issue_number}/regenerate")
async def regenerate_issue(issue_number: int):
    """Re-render an issue's art from its saved script (same text and layout, new panels).
    A displayed issue is redrawn on the device only when it next fetches that issue."""
    comic = db.get_comic(issue_number)
    if not comic:
        raise HTTPException(status_code=404, detail=f"Issue #{issue_number} not found")
    script = {**json.loads(comic["script_json"]), "issue_number": issue_number,
              "series_issue": comic["series_issue"]}

    async def rerender():
        async with generation_lock:
            try:
                await asyncio.to_thread(comic_compositor.compose_comic, script)
                logger.info(f"Issue #{issue_number} regenerated.")
            except Exception as e:
                logger.error(f"Regenerating issue #{issue_number} failed: {e}")

    asyncio.create_task(rerender())
    return {"status": "regenerating", "issue_number": issue_number}


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
    
    vad.reset_silence()

    # Same path as real speech: scramble, then mine; only mined rows are stored
    from server.privacy.pos_scrambler import scramble_text
    scrambled = await asyncio.to_thread(scramble_text, text, settings.POS_SCRAMBLE_RATE)
    await mine_and_store(scrambled, time.time(), duration)
    return {"status": "simulated"}
