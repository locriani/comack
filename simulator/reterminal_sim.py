"""
Desktop reTerminal E1001 Hardware Simulator.
Emulates the reTerminal E1001 on macOS/Linux:
- Captures/synthesizes 16kHz audio and streams over WebSocket to Comack server
- Displays the 800x480 e-Paper display with authentic e-ink refresh flashing
- Reacts to 'SNEAKY_COMIC_REFRESH' events
- Puts the device's three front buttons on the keyboard: ←/→ (or b/f) page
  through revealed issues, g (or space) is the green trigger
"""
import sys
import time
import math
import struct
import json
import asyncio
import argparse
import queue
import threading
import urllib.request
from pathlib import Path
from io import BytesIO
from PIL import Image

try:
    import websockets
except ImportError:
    websockets = None


def generate_audio_chunk(sample_rate: int = 16000, duration: float = 0.1, is_speech: bool = False) -> bytes:
    """Generate 100ms of 16-bit signed PCM audio."""
    num_samples = int(sample_rate * duration)
    samples = []
    
    if is_speech:
        # Complex multi-frequency wave mimicking human vocal formants (200Hz, 700Hz, 2500Hz)
        for i in range(num_samples):
            t = i / sample_rate
            val = (
                math.sin(2 * math.pi * 220 * t) * 0.4 +
                math.sin(2 * math.pi * 700 * t) * 0.3 +
                math.sin(2 * math.pi * 2400 * t) * 0.15
            ) * 12000.0
            val = max(-32767, min(32767, int(val)))
            samples.append(val)
    else:
        # Low ambient room noise
        import random
        for _ in range(num_samples):
            val = int(random.gauss(0, 150))
            val = max(-32767, min(32767, val))
            samples.append(val)
            
    return struct.pack(f"<{len(samples)}h", *samples)


async def audio_streamer_task(server_url: str, simulate_speech_interval: float = 25.0):
    """Streams continuous 16kHz PCM audio to the server."""
    ws_url = f"{server_url}/ws/audio"
    print(f"[Sim Audio] Connecting to {ws_url}...")
    
    while True:
        try:
            async with websockets.connect(ws_url) as ws:
                print("[Sim Audio] Audio streaming active.")
                start_time = time.time()
                while True:
                    elapsed = time.time() - start_time
                    # Cycle: 5s speech, 25s silence
                    cycle_time = elapsed % (simulate_speech_interval + 5.0)
                    is_talking = cycle_time < 5.0
                    
                    chunk = generate_audio_chunk(is_speech=is_talking)
                    await ws.send(chunk)
                    await asyncio.sleep(0.098)
        except Exception as e:
            print(f"[Sim Audio] Disconnected ({e}), retrying in 2s...")
            await asyncio.sleep(2.0)


async def event_listener_task(server_url: str, on_refresh=None):
    """Listens for SNEAKY_COMIC_REFRESH events and updates display."""
    ws_url = f"{server_url}/ws/events"
    print(f"[Sim EPD] Connecting to {ws_url}...")
    
    while True:
        try:
            async with websockets.connect(ws_url) as ws:
                print("[Sim EPD] Event listener active.")
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    if data.get("event") == "SNEAKY_COMIC_REFRESH":
                        print(f"\n[Sim EPD] *** SNEAKY REFRESH TRIGGERED! Issue #{data.get('issue_number')} ***")
                        print(f"Room was silent for {data.get('silence_seconds')}s.")
                        if on_refresh:
                            on_refresh(data)
        except Exception as e:
            await asyncio.sleep(2.0)


# --- Front buttons: keyboard stand-ins for the device's three buttons ---

REVEALED_STATUS = "DISPLAYED"  # held (STEALTH_HOLD) comics stay hidden, as on the device


def fetch_json(url: str, timeout: float = 4.0):
    """GET and parse JSON; None on any failure (logged, never raised)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[Sim Buttons] GET {url} failed: {e}")
        return None


def post_json(url: str, payload: dict, timeout: float = 4.0) -> bool:
    """POST JSON fire-and-forget: failures are logged, never raised — a press
    must never block or crash the demo (the firmware PressReporter contract)."""
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception as e:
        print(f"[Sim Buttons] POST {url} failed: {e}")
        return False


class SimNavigator:
    """Keyboard-side equivalent of the firmware Navigator.

    Mirrors `Navigator::browse_targets` (firmware/crates/comack-core/src/domain/
    navigator.rs): candidates come nearest first, deleted or missing issues are
    gaps to walk past, and the walk never goes beyond the latest revealed
    issue. `shown` is the issue on screen; green ends the browse so the next
    show is the latest.
    """

    def __init__(self, server_base: str):
        self.server_base = server_base
        self.latest = None    # latest revealed issue the server knows (Navigator::latest)
        self.shown = None     # issue currently on screen (Navigator::shown)
        self.browsing = False

    def report_latest(self, issue):
        """Record the server's latest revealed issue (Navigator::report_latest)."""
        if issue is not None:
            self.latest = issue
            if self.shown is None:
                self.shown = issue

    def revealed_issues(self):
        """Ascending revealed issue numbers.

        Primary: /api/comics/history — the same enumeration the dashboard
        Archive view uses, filtered to revealed issues. Fallback: walk the
        known issue-number range (1..=latest) when the history route answers
        nothing usable (it is token-gated under WEB_TOKEN, while per-issue
        fetches are tokenless); missing files are skipped by the fetch walk
        either way.
        """
        history = fetch_json(f"{self.server_base}/api/comics/history?limit=200")
        if isinstance(history, list):
            revealed = [c["issue_number"] for c in history if c.get("status") == REVEALED_STATUS]
            if revealed:
                return sorted(revealed)
        return list(range(1, (self.latest or 0) + 1))

    def candidates(self, direction: int):
        """Candidate issues for one step in `direction` (+1 forward, -1 back),
        nearest first; empty at the range end."""
        if self.shown is None:
            return []
        revealed = self.revealed_issues()
        if direction > 0:
            return [n for n in revealed if n > self.shown]
        return [n for n in reversed(revealed) if n < self.shown]

    def end_browse(self):
        """Green: stop browsing so the next show is the latest (Navigator::end_browse)."""
        self.browsing = False


class PressQueue:
    """Sequential press worker.

    Key handlers enqueue; network work happens off the tkinter thread, one
    press at a time — a press that lands while another fetch is in flight
    queues up and then fires (like the device's latched presses) instead of
    racing or being dropped.
    """

    def __init__(self):
        self.jobs: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def submit(self, job):
        self.jobs.put(job)

    def _worker(self):
        while True:
            job = self.jobs.get()
            try:
                job()
            except Exception as e:  # a press must never kill the demo
                print(f"[Sim Buttons] Press handling failed: {e}")


def run_gui_simulator(server_host: str, server_port: int):
    """Run interactive GUI window simulating the 800x480 e-paper."""
    import tkinter as tk
    from PIL import ImageTk
    
    root = tk.Tk()
    root.title("reTerminal E1001 Simulator (800x480 e-Paper)")
    root.geometry("840x620")
    root.configure(bg="#22252e")
    
    # Outer bezel frame
    frame = tk.Frame(root, bg="#d8dbe2", bd=10, relief="ridge")
    frame.pack(padx=20, pady=20)
    
    canvas = tk.Label(frame, bg="#ffffff", width=800, height=480)
    canvas.pack()
    
    status_label = tk.Label(root, text=f"Status: Connected | Listening to Comack on {server_host}:{server_port}",
                            bg="#22252e", fg="#9ca3af", font=("Courier", 11))
    status_label.pack(pady=4)
    
    # Key-bindings footer (the device's buttons, as printed on its bezel)
    keys_label = tk.Label(root, text="←/→ or b/f: page back/forward   ·   g or space: green (new comic)",
                          bg="#22252e", fg="#6b7280", font=("Courier", 10))
    keys_label.pack(pady=(0, 10))
    
    server_base = f"http://{server_host}:{server_port}"
    nav = SimNavigator(server_base)
    presses = PressQueue()
    
    def fetch_current_image():
        try:
            url = f"{server_base}/api/comics/current.png?t={time.time()}"
            with urllib.request.urlopen(url, timeout=4) as resp:
                img_data = resp.read()
                pil_img = Image.open(BytesIO(img_data)).convert("RGB")
                return pil_img
        except Exception as e:
            print(f"[Sim EPD] Current comic unavailable: {e}")
            return Image.new("RGB", (800, 480), (255, 255, 255))
            
    def show_image(pil_img):
        tk_img = ImageTk.PhotoImage(pil_img)
        canvas.configure(image=tk_img)
        canvas.image = tk_img

    def set_status(text):
        status_label.config(text=text)

    def fetch_issue_image(issue):
        """Fetch one revealed issue's page; None when it is a gap to walk past."""
        try:
            url = f"{server_base}/api/comics/{issue}/png"
            with urllib.request.urlopen(url, timeout=8) as resp:
                return Image.open(BytesIO(resp.read())).convert("RGB")
        except Exception as e:
            print(f"[Sim Buttons] Issue #{issue} unavailable: {e}")
            return None

    def report_press(button):
        """Fire-and-forget press report, the dashboard chip's source. Until the
        server route exists the POST is a logged no-op — like the firmware's
        reporter, it must never block or fail the press."""
        post_json(f"{server_base}/api/device/buttons", {"button": button})

    def refresh_latest():
        current = fetch_json(f"{server_base}/api/comics/current")
        nav.report_latest(current.get("issue_number") if isinstance(current, dict) else None)

    def flash_then(callback):
        """The authentic e-paper refresh dance: black → white → the new frame."""

        def flash(color, nxt):
            tk_color = ImageTk.PhotoImage(Image.new("RGB", (800, 480), color))
            canvas.configure(image=tk_color)
            canvas.image = tk_color
            root.update()
            root.after(150, nxt)

        def flash_black():
            flash((0, 0, 0), flash_white)

        def flash_white():
            flash((255, 255, 255), callback)

        flash_black()

    def update_display(event_data=None):
        if not event_data:
            show_image(fetch_current_image())
            return

        # Simulate e-paper inversion flash before revealing the new issue
        def show_comic():
            show_image(fetch_current_image())
            set_status(f"EPD Refreshed: Issue #{event_data.get('issue_number')} • Silent for {event_data.get('silence_seconds')}s")

        flash_then(show_comic)

    def navigate_job(direction, button):
        """One browse step: report the press, then walk candidates nearest
        first, fetching each issue's page and showing the first that fetches —
        the firmware ComicViewer::on_navigate loop."""
        report_press(button)
        refresh_latest()
        start = nav.shown
        for target in nav.candidates(direction):
            img = fetch_issue_image(target)
            if img is None:
                continue  # deleted or missing issue: a gap to walk past
            nav.shown = target
            nav.browsing = True

            def show_browsed():
                show_image(img)
                word = "newer" if target > start else "older"
                set_status(f"Browsing: issue #{target} ({word} than #{start}) — g or space returns to the latest")

            root.after(0, flash_then, show_browsed)
            return
        word = "newer" if direction > 0 else "older"
        root.after(0, set_status, f"No {word} revealed issue — staying on #{start}")

    def green_job():
        """The green trigger, as the device fires it: report the press, then
        the same /api/trigger call, then refetch the current comic this tick.
        An unchanged issue is not re-flashed (the device's rule) — when a
        comic is still generating, the SNEAKY_COMIC_REFRESH event flashes it
        in when the server reveals it."""
        nav.end_browse()
        report_press("trigger")
        post_json(f"{server_base}/api/trigger", {})
        current = fetch_json(f"{server_base}/api/comics/current")
        issue = current.get("issue_number") if isinstance(current, dict) else None
        img = fetch_current_image()

        def show_green():
            if issue is None:
                set_status("Green pressed — nothing revealed yet (no current comic)")
            elif nav.shown == issue:
                set_status(f"Green pressed — issue #{issue} already on screen; the new one flashes in when revealed")
            else:
                nav.report_latest(issue)
                nav.shown = issue

                def show_green_frame():
                    show_image(img)
                    set_status(f"EPD Refreshed: issue #{issue} (green trigger)")

                flash_then(show_green_frame)

        root.after(0, show_green)

    def press(button):
        if button in ("back", "forward"):
            direction = -1 if button == "back" else 1
            presses.submit(lambda: navigate_job(direction, button))
        else:
            presses.submit(green_job)

    # The keyboard is the button panel: device vocabulary forward/back/trigger
    for key, button in (
        ("<Left>", "back"), ("<Right>", "forward"),
        ("b", "back"), ("f", "forward"),
        ("g", "trigger"), ("<space>", "trigger"),
    ):
        root.bind(key, lambda event, b=button: press(b))

    update_display()
    refresh_latest()  # know the latest revealed issue before the first keypress
    
    # Run async background threads for websocket tasks
    def start_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ws_base = f"ws://{server_host}:{server_port}"
        loop.create_task(audio_streamer_task(ws_base))
        loop.create_task(event_listener_task(ws_base, on_refresh=on_refresh_event))
        loop.run_forever()

    def on_refresh_event(event_data):
        # A server reveal puts the latest issue on screen; browsing is over
        issue = event_data.get("issue_number")
        nav.report_latest(issue)
        nav.shown = issue
        nav.browsing = False
        root.after(0, update_display, event_data)

    t = threading.Thread(target=start_loop, daemon=True)
    t.start()
    
    root.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="reTerminal E1001 Desktop Simulator")
    parser.add_argument("--host", default="localhost", help="Comack server host")
    parser.add_argument("--port", type=int, default=8000, help="Comack server port")
    parser.add_argument("--headless", action="store_true", help="Run without GUI (audio stream only)")
    args = parser.parse_args()
    
    if args.headless:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ws_base = f"ws://{args.host}:{args.port}"
        loop.create_task(audio_streamer_task(ws_base))
        loop.create_task(event_listener_task(ws_base))
        loop.run_forever()
    else:
        run_gui_simulator(args.host, args.port)
