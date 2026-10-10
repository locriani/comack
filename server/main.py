"""
Comack Main Server.
FastAPI application with WebSocket audio streaming, real-time VAD,
30-minute comic cycle scheduler, sneaky silence dispatcher, and REST API.
"""
import re
import time
import json
import socket
import asyncio
import logging
from pathlib import Path
from typing import Set, Dict, Any, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager

from server.devices import (
    AUDIO_STREAMER,
    EVENT_SUBSCRIBER,
    MAX_NAME_LENGTH,
    device_registry,
    display_name,
    generate_device_token,
    hash_device_token,
    public_device,
    resolve_device,
    unique_device_id,
)
from server.auth import authorized
from server.config import settings, warn_on_unknown_env, COMICS_DIR, STATIC_DIR
from server.config_api import router as config_router
from server.config_test import router as config_test_router
from server.storage import DEFAULT_DEVICE_ID, db
from server.pipelines import pipelines
from server.privacy.thematic_miner import mine_sentences
from server.privacy.pos_scrambler import pools
from server.llm import prompt_log, expire_prompt_log
from server.story.narrative_engine import engine_for
from server.render.comic_compositor import comic_compositor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("comack.main")

# Active WebSocket connections
event_subscribers: Set[WebSocket] = set()


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


async def mine_and_store(scrambled: str, timestamp: float, duration: float, device_id: str):
    """Scrambled transcript -> LLM content per sentence -> second 25% swap -> the only thing stored.
    Rows carry the device whose pipeline heard the speech (spec V1 attribution)."""
    for row in await mine_sentences(scrambled):
        logger.info(f"Mined Story Element: {row}")
        t_id = db.save_transcript(timestamp, duration, row, device_id)
        broadcast_event({"event": "NEW_TRANSCRIPT", "transcript": {
            "id": t_id, "timestamp": timestamp, "duration_seconds": duration, "scrambled_text": row,
            "device_id": device_id}})


generation_lock = asyncio.Lock()  # one comic at a time: FLUX can't share the GPU, and issue numbers must not collide


async def run_comic_generation_cycle(device_id: str = DEFAULT_DEVICE_ID) -> Dict[str, Any]:
    """One comic for one device. The device's engine scopes the arc, the comic
    inherits its arc's device, and the queued issue waits in that device's own
    dispatcher slot. The lock stays house-global: FLUX can't share the GPU, and
    issue numbers are one sequence whatever the device."""
    async with generation_lock:
        return await _generation_cycle(device_id)


async def _generation_cycle(device_id: str) -> Dict[str, Any]:
    """Execute a comic generation cycle for one device."""
    logger.info(f"Executing comic generation cycle for device '{device_id}'...")

    # 1. Fetch this device's unconsumed scrambled transcripts
    unprocessed = db.get_unprocessed_transcripts(device_id)
    logger.info(f"Gathered {len(unprocessed)} dialogue snippets for this cycle.")

    # 2. Generate script with narrative continuity
    script = await engine_for(device_id).generate_comic_script(unprocessed)
    
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
    
    db.set_state(f"lines_wanted:{device_id}", str(script["lines_wanted_next"]))

    # 5. Mark transcripts as used
    if unprocessed:
        transcript_ids = [t["id"] for t in unprocessed]
        db.mark_transcripts_used(transcript_ids, comic_id)
        
    comic_record = {
        "id": comic_id,
        "issue_number": script["issue_number"],
        "series_issue": script["series_issue"],
        "device_id": device_id,
        "title": script["title"],
        "layout_type": script["layout_type"],
        "panel_count": script["panel_count"],
        "created_at": time.time(),
        "status": "STEALTH_HOLD",
        "script": script,
        "png_url": f"/api/comics/{script['issue_number']}/png",
        "bin_url": f"/api/comics/{script['issue_number']}/bin"
    }
    
    # 6. Queue comic in this device's sneaky slot
    pipelines.for_device(device_id).dispatcher.queue_comic(comic_record)
    
    # Broadcast to clients
    broadcast_event({
        "event": "NEW_COMIC_QUEUED",
        "comic": comic_record,
        "status": pipelines.for_device(device_id).dispatcher.get_status()
    })
    
    return comic_record


_trigger_running: Dict[str, bool] = {}  # per device: one refresh per phrase burst


async def _trigger_cycle_and_dispatch(device_id: str = DEFAULT_DEVICE_ID):
    """Refresh this device's display now: reveal a held comic instantly, otherwise generate one and reveal it."""
    if _trigger_running.get(device_id):  # the phrase often lands in several segments; one refresh is enough
        return
    _trigger_running[device_id] = True
    try:
        async with generation_lock:  # a comic mid-generation is the one to reveal
            pass
        dispatcher = pipelines.for_device(device_id).dispatcher
        if dispatcher.force_dispatch():
            logger.info("⚡ Held comic revealed immediately.")
            return
        logger.info("⚡ Nothing held; generating a comic for immediate display...")
        await run_comic_generation_cycle(device_id)
        dispatcher.force_dispatch()
        logger.info("⚡ Immediate comic generation completed and dispatched to display!")
    except Exception as e:
        logger.error(f"Error executing immediate comic cycle: {e}")
    finally:
        _trigger_running[device_id] = False


def handle_secret_trigger(device_id: str, phrase: str):
    """A device's TriggerListener heard a phrase: refresh THAT device's display now."""
    logger.warning(f"🚨 [SECRET TRIGGER ACTIVATED on '{device_id}'] '{phrase.upper()}' detected in speech! "
                   f"Generating new frame immediately.")
    broadcast_event({
        "event": "SECRET_TRIGGER_ACTIVATED",
        "device_id": device_id,
        "phrase": phrase,
        "message": f"Secret phrase '{phrase}' recognized! Generating new comic frame immediately."
    })
    asyncio.create_task(_trigger_cycle_and_dispatch(device_id))


# The pipeline registry's two side effects live here: sneaky releases broadcast
# to every event subscriber; a heard phrase refreshes the hearing device's display.
pipelines.on_dispatch = broadcast_event
pipelines.handle_trigger = handle_secret_trigger


def comic_due(since_last_s: float, have_lines: int, wanted_lines: int) -> bool:
    """Generate once the requested room notes are in, bounded by the min/max gap between comics."""
    if since_last_s >= settings.COMIC_MAX_MINUTES * 60:
        return True
    return since_last_s >= settings.COMIC_MIN_MINUTES * 60 and have_lines >= wanted_lines


async def periodic_comic_scheduler():
    """Checks every 30s whether each connected device's next comic is due (see comic_due)."""
    started = time.time()
    logger.info(f"Comic scheduler started: {settings.COMIC_MIN_MINUTES}-{settings.COMIC_MAX_MINUTES} min between comics.")
    while True:
        try:
            await asyncio.sleep(30)
            await scheduler_tick(started)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic comic scheduler: {e}")


async def scheduler_tick(started: float) -> None:
    """One scheduler pass: every connected device's due-ness, in one loop.

    Due-ness is per device — that device's last comic, that device's
    unprocessed room lines, that device's lines_wanted pacing key — but
    generation still funnels through the house-global generation_lock: the GPU
    is shared and issue numbers are one sequence, so two devices due at the
    same moment serialize rather than race.
    """
    for pipe in pipelines.active():
        device_id = pipe.device_id
        last = db.get_recent_comics(device_id=device_id, limit=1)
        since = time.time() - (last[0]["created_at"] if last else started)
        wanted = int(db.get_state(f"lines_wanted:{device_id}", str(settings.DEFAULT_LINES_WANTED)))
        if not generation_lock.locked() and comic_due(since, len(db.get_unprocessed_transcripts(device_id)), wanted):
            await run_comic_generation_cycle(device_id=device_id)


async def periodic_prompt_log_expiry():
    """Debug prompt log entries are gone within 30s of turning 15 minutes old."""
    while True:
        await asyncio.sleep(30)
        expire_prompt_log()


async def periodic_transcription():
    """Every PROCESS_INTERVAL_MINUTES: drain each device's batch, transcribe, scramble, mine and store."""
    while True:
        try:
            await asyncio.sleep(settings.PROCESS_INTERVAL_MINUTES * 60)
            for pipe in pipelines.all():
                try:
                    batch = pipe.transcriber.take()
                    if not batch:
                        continue
                    result = await asyncio.to_thread(pipe.transcriber.transcribe_batch, *batch)
                    del batch
                    if result:
                        await mine_and_store(*result, device_id=pipe.device_id)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    # One device's failed batch never starves another room's lines.
                    logger.error(f"Transcription failed for device '{pipe.device_id}': {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic transcription: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 0. Surface config drift before anything reads a setting
    warn_on_unknown_env()

    # Pipelines are created per device on first use. The default device's lane
    # exists from boot, so a tokenless deployment behaves exactly as before —
    # and any comic held in stealth when the server last stopped re-enters its
    # queue here (server/pipelines.py).
    pipelines.for_device(DEFAULT_DEVICE_ID)

    # Ensure at least one initial comic exists so the screen is never blank; in the background,
    # so the API serves while it renders. The default device until identity-aware delivery lands.
    if not db.get_current_displayed_comic(DEFAULT_DEVICE_ID) and not db.get_stealth_hold_comic(DEFAULT_DEVICE_ID):
        logger.info("No existing comic found. Generating initial comic Issue #001...")
        asyncio.create_task(_trigger_cycle_and_dispatch())

    cycle_task = asyncio.create_task(periodic_comic_scheduler())
    transcribe_task = asyncio.create_task(periodic_transcription())
    expiry_task = asyncio.create_task(periodic_prompt_log_expiry())
    asyncio.create_task(asyncio.to_thread(pools))  # build scrambler word pools off the event loop
    
    yield
    
    # Teardown
    for pipe in pipelines.all():
        pipe.dispatcher.stop()
    transcribe_task.cancel()  # ponytail: up to one interval of unprocessed speech is dropped on restart
    expiry_task.cancel()
    if cycle_task:
        cycle_task.cancel()


app = FastAPI(title="Comack", lifespan=lifespan)
app.include_router(config_router)
app.include_router(config_test_router)


@app.middleware("http")
async def bearer_auth_middleware(request: Request, call_next):
    """Optional WEB_TOKEN gate for browser-facing surfaces (server/auth.py owns the split:
    device audio, comic reads and static assets stay tokenless)."""
    if not authorized(request.url.path, request.headers.get("authorization"), request.query_params.get("token")):
        return JSONResponse(
            {"detail": "Unauthorized: this server requires the WEB_TOKEN bearer token."},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await call_next(request)


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

    Identity gate (spec D1/D2): the stream resolves before accept — a valid
    ?device_id=&device_token= pair binds it to that device, absent identity binds
    the auto 'default' device, and identity presented but wrong is closed with
    4401 (the same code /ws/events uses). Never a silent fallback: a mistyped
    token must not quietly route one room's audio into another room's story.
    """
    device_id_param = websocket.query_params.get("device_id")
    device_token_param = websocket.query_params.get("device_token")
    device = resolve_device(device_id_param, device_token_param)
    if device is None:
        await websocket.close(code=4401)
        return
    presented_identity = bool(device_id_param or device_token_param)
    await websocket.accept()
    # Identified connections wear the registry name (server-verified beats
    # client-supplied); tokenless connections keep the ?name=/role label.
    device_registry.connect(
        websocket,
        device["name"] if presented_identity else display_name(websocket.query_params.get("name"), AUDIO_STREAMER),
        ip=websocket.client.host,
        now=time.time(),
        device_id=device["id"],
    )
    # This device's audio lane: its own VAD, trigger window, transcript batch,
    # and dispatcher slot. One stream per device in practice; a second would
    # interleave into the same room's lane (and VAD), undefined by design.
    pipeline = pipelines.for_device(device["id"])
    pipeline.connected = True
    logger.info(f"reTerminal E1001 audio streamer connected: device '{device['id']}'.")
    last_seen_write = time.time()
    db.touch_device(device["id"])
    try:
        while True:
            data = await websocket.receive_bytes()
            now = time.time()
            device_registry.touch(websocket, now=now)
            if now - last_seen_write >= 60:  # throttled: chunks arrive continuously, the DB must not
                db.touch_device(device["id"])
                last_seen_write = now
            if data:
                is_speech, silence_duration = pipeline.vad.process_chunk(data)
                pipeline.trigger_listener.feed(data, is_speech)
    except WebSocketDisconnect:
        logger.info("reTerminal audio stream disconnected.")
    except Exception as e:
        logger.warning(f"Audio stream error: {e}")
    finally:
        pipeline.connected = False
        device_registry.disconnect(websocket)


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    """
    Subscribes reTerminal E1001 and Web UI to status updates and sneaky refresh events.
    With WEB_TOKEN set, the browser event feed appends ?token= (WebSocket clients
    cannot send headers); the tokenless hardware uses /ws/audio instead.
    """
    if not authorized(websocket.url.path, websocket.headers.get("authorization"), websocket.query_params.get("token")):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    event_subscribers.add(websocket)
    device_registry.connect(
        websocket,
        display_name(websocket.query_params.get("name"), EVENT_SUBSCRIBER),
        ip=websocket.client.host,
        now=time.time(),
    )
    try:
        # Send initial snapshot
        await websocket.send_text(json.dumps({
            "event": "INIT_SNAPSHOT",
            "sneaky_status": pipelines.for_device(DEFAULT_DEVICE_ID).dispatcher.get_status(),
            # House-global surfaces show the default device until per-device
            # delivery and UI land (spec: delivery, web UI).
            "current_comic": db.get_current_displayed_comic(DEFAULT_DEVICE_ID),
            "active_arc": db.get_active_arc(DEFAULT_DEVICE_ID)
        }))
        while True:
            # Handle client heartbeats or incoming commands
            msg = await websocket.receive_text()
            device_registry.touch(websocket, now=time.time())
            data = json.loads(msg)
            if data.get("cmd") == "PING":
                await websocket.send_text(json.dumps({
                    "event": "PONG",
                    "sneaky_status": pipelines.for_device(DEFAULT_DEVICE_ID).dispatcher.get_status()
                }))
    except WebSocketDisconnect:
        event_subscribers.discard(websocket)
    except Exception:
        event_subscribers.discard(websocket)
    finally:
        # The snapshot send is inside the try so a failed first push still
        # reaps the registry entry (event_subscribers has the same gap, kept
        # as-is — this endpoint only guarantees its own bookkeeping).
        device_registry.disconnect(websocket)


# --- REST Endpoints ---

@app.get("/api/status")
async def get_system_status():
    # Default device until per-device delivery and UI land (spec: delivery, web UI).
    arc = db.get_active_arc(DEFAULT_DEVICE_ID)
    current = db.get_current_displayed_comic(DEFAULT_DEVICE_ID)
    sneaky = pipelines.for_device(DEFAULT_DEVICE_ID).dispatcher.get_status()
    
    return {
        "status": "online",
        "timestamp": time.time(),
        "identity": {
            "hostname": socket.gethostname(),
            "port": settings.PORT,
            "display": f"{settings.DISPLAY_WIDTH}×{settings.DISPLAY_HEIGHT} e-paper",
        },
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


@app.get("/api/devices")
async def get_devices():
    """The device registry (server/devices.py) plus live visibility.

    `devices` lists every registered device — id, name, created_at,
    last_seen_at, its live `connections` (the audio stream attributed to
    it), and its story state (`active_arc`, `current_comic`: the issue its
    display is showing). `unregistered_connections` holds live sockets that present no device
    identity — dashboard event subscribers. The first check when a device
    seems unreachable (docs/troubleshooting.md). Gated by WEB_TOKEN like every
    other /api route: unlike the device-critical paths this one is
    browser-facing, so it is never tokenless when a token is set."""
    connections = device_registry.snapshot()
    devices = []
    for row in db.get_devices():
        device = public_device(row)
        device["connections"] = [c for c in connections if c["device_id"] == row["id"]]
        # This device's story state for the dashboard's per-device cards: its
        # own active arc and the issue its display is showing (spec V8 — the
        # UI groups by device, never by house).
        device["active_arc"] = db.get_active_arc(row["id"])
        device["current_comic"] = db.get_current_displayed_comic(row["id"])
        devices.append(device)
    return {
        "devices": devices,
        "unregistered_connections": [c for c in connections if c["device_id"] is None],
    }


class DeviceCreate(BaseModel):
    """Body of a device-registration request. The token is generated here and
    returned exactly once — only its hash is stored (server/devices.py)."""

    name: str


class DeviceRename(BaseModel):
    """Body of a rename: the user-visible label only — ids are immutable (they
    travel in device credentials and are attributed to stored rows)."""

    name: str


def _validated_device_name(raw_name: str) -> str:
    name = raw_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Device name must not be blank.")
    if len(name) > MAX_NAME_LENGTH:
        raise HTTPException(status_code=400, detail=f"Device name must be at most {MAX_NAME_LENGTH} characters.")
    return name


@app.post("/api/devices")
async def create_device(body: DeviceCreate):
    """Register a device: server-issued id derived from the name, a per-device
    token shown once in this response (the hash is stored, never the token)."""
    name = _validated_device_name(body.name)
    device_id = unique_device_id(name)
    token = generate_device_token()
    db.create_device(device_id, name, hash_device_token(token))
    logger.info(f"Device registered: '{name}' (id: {device_id}).")
    return {"id": device_id, "name": name, "token": token}


@app.patch("/api/devices/{device_id}")
async def rename_device(device_id: str, body: DeviceRename):
    """Rename a registered device. Returns the public row, token never."""
    name = _validated_device_name(body.name)
    if not db.rename_device(device_id, name):
        raise HTTPException(status_code=404, detail=f"Unknown device: {device_id}")
    return public_device(db.get_device(device_id))


@app.post("/api/trigger")
async def trigger_secret_phrase(request: Request, payload: dict = None):
    """Manually invoke secret trigger (e.g. 'alpha protocol') to generate a new frame
    immediately. Identity-aware (spec D2): the refresh is attributed to the asking
    device; identity presented but wrong is a 401, never a fallback to default."""
    device = requesting_device(request)
    phrase = (payload or {}).get("phrase", "alpha protocol")
    logger.warning(f"🚨 Manual trigger invoked on '{device['id']}': '{phrase}'")
    broadcast_event({
        "event": "SECRET_TRIGGER_ACTIVATED",
        "device_id": device["id"],
        "phrase": phrase,
        "message": f"Secret trigger '{phrase}' activated. Generating immediate comic frame!"
    })
    asyncio.create_task(_trigger_cycle_and_dispatch(device["id"]))
    return {"status": "ok", "message": f"Trigger '{phrase}' activated. Frame generation started."}


class ButtonPress(BaseModel):
    """Body of a device button-press report. The empty default (a missing field)
    fails the whitelist below with the same 400 as an unknown button."""

    button: str = ""


@app.post("/api/device/buttons")
async def device_buttons(body: ButtonPress):
    """A device press reports itself here (fire-and-forget) so the dashboard can
    show it. Tokenless like the other device-critical paths (server/auth.py):
    the firmware has no token capability. Single device today, so the report
    carries no device identity."""
    if body.button not in {"forward", "back", "trigger"}:
        raise HTTPException(status_code=400, detail="unknown button")
    logger.info(f"Device button press: {body.button}")
    broadcast_event({"event": "BUTTON_PRESSED", "button": body.button})
    return {"ok": True}


def requesting_device(request: Request):
    """The asking device for an HTTP request (spec D2): a valid
    ?device_id=&device_token= pair binds that device, absent identity binds the auto
    'default' device (browsers, legacy firmware), and identity presented but wrong is
    a 401 — the HTTP twin of the WebSocket 4401 gate. Never a silent fallback: a
    mistyped token must not route one room's display to another room's comic."""
    device = resolve_device(request.query_params.get("device_id"),
                            request.query_params.get("device_token"))
    if device is None:
        raise HTTPException(status_code=401, detail="Unknown device_id or invalid device_token")
    return device


@app.get("/api/comics/current")
async def get_current_comic(request: Request):
    device = requesting_device(request)
    comic = db.get_current_displayed_comic(device["id"])
    if not comic:
        raise HTTPException(status_code=404, detail="No displayed comic yet")
    return comic


def _displayed_issue(device_id: str) -> int:
    comic = db.get_current_displayed_comic(device_id)
    if not comic:
        raise HTTPException(status_code=404, detail="No displayed comic yet")
    return comic["issue_number"]


@app.get("/api/comics/current.png")
async def get_current_comic_png(request: Request):
    return await get_issue_png(_displayed_issue(requesting_device(request)["id"]))


@app.get("/api/comics/current.bin")
async def get_current_comic_bin(request: Request):
    return await get_issue_bin(_displayed_issue(requesting_device(request)["id"]))


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


@app.get("/api/comics/{issue_number}/{original}.png")
async def get_issue_original(issue_number: int, original: str):
    """The saved originals: the color page before halftoning, and each panel's raw model art."""
    path = COMICS_DIR / f"issue_{issue_number:04d}_{original}.png"
    if not re.fullmatch(r"color|panel\d", original) or not path.exists():
        raise HTTPException(status_code=404, detail=f"Issue #{issue_number} has no {original} image")
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
    comic = pipelines.for_device(DEFAULT_DEVICE_ID).dispatcher.force_dispatch()
    if not comic:
        return {"status": "none_pending", "message": "No comic was waiting in stealth queue."}
    return {"status": "dispatched", "comic": comic}


@app.post("/api/test/simulate_speech")
async def simulate_speech_dialogue(payload: Dict[str, Any]):
    """Inject a test phrase into the pipeline for validation."""
    text = payload.get("text", "The quick brown fox jumps over the lazy dog.")
    duration = float(payload.get("duration", 2.5))
    
    pipelines.for_device(DEFAULT_DEVICE_ID).vad.reset_silence()

    # Same path as real speech: scramble, then mine; only mined rows are stored
    from server.privacy.pos_scrambler import scramble_text
    scrambled = await asyncio.to_thread(scramble_text, text, settings.POS_SCRAMBLE_RATE)
    await mine_and_store(scrambled, time.time(), duration, DEFAULT_DEVICE_ID)
    return {"status": "simulated"}
