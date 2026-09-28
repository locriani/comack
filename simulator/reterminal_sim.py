"""
Desktop reTerminal E1001 Hardware Simulator.
Emulates the reTerminal E1001 on macOS/Linux:
- Captures/synthesizes 16kHz audio and streams over WebSocket to Comack server
- Displays the 800x480 e-Paper display with authentic e-ink refresh flashing
- Reacts to 'SNEAKY_COMIC_REFRESH' events
"""
import sys
import time
import math
import struct
import json
import asyncio
import argparse
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


def run_gui_simulator(server_host: str, server_port: int):
    """Run interactive GUI window simulating the 800x480 e-paper."""
    import tkinter as tk
    from PIL import ImageTk
    
    root = tk.Tk()
    root.title("reTerminal E1001 Simulator (800x480 e-Paper)")
    root.geometry("840x540")
    root.configure(bg="#22252e")
    
    # Outer bezel frame
    frame = tk.Frame(root, bg="#d8dbe2", bd=10, relief="ridge")
    frame.pack(padx=20, pady=20)
    
    canvas = tk.Label(frame, bg="#ffffff", width=800, height=480)
    canvas.pack()
    
    status_label = tk.Label(root, text=f"Status: Connected | Listening to Comack on {server_host}:{server_port}",
                            bg="#22252e", fg="#9ca3af", font=("Courier", 11))
    status_label.pack(pady=4)
    
    server_base = f"http://{server_host}:{server_port}"
    
    def fetch_current_image():
        try:
            url = f"{server_base}/api/comics/current.png?t={time.time()}"
            with urllib.request.urlopen(url, timeout=4) as resp:
                img_data = resp.read()
                pil_img = Image.open(BytesIO(img_data)).convert("RGB")
                return pil_img
        except Exception as e:
            return Image.new("RGB", (800, 480), (255, 255, 255))
            
    def update_display(event_data=None):
        # Simulate e-paper inversion flash
        def flash_black():
            blk = Image.new("RGB", (800, 480), (0, 0, 0))
            tk_blk = ImageTk.PhotoImage(blk)
            canvas.configure(image=tk_blk)
            canvas.image = tk_blk
            root.update()
            root.after(150, flash_white)
            
        def flash_white():
            wht = Image.new("RGB", (800, 480), (255, 255, 255))
            tk_wht = ImageTk.PhotoImage(wht)
            canvas.configure(image=tk_wht)
            canvas.image = tk_wht
            root.update()
            root.after(150, show_comic)
            
        def show_comic():
            pil_img = fetch_current_image()
            tk_img = ImageTk.PhotoImage(pil_img)
            canvas.configure(image=tk_img)
            canvas.image = tk_img
            if event_data:
                status_label.config(text=f"EPD Refreshed: Issue #{event_data.get('issue_number')} • Silent for {event_data.get('silence_seconds')}s")

        if event_data:
            flash_black()
        else:
            show_comic()

    update_display()
    
    # Run async background threads for websocket tasks
    import threading
    def start_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ws_base = f"ws://{server_host}:{server_port}"
        loop.create_task(audio_streamer_task(ws_base))
        loop.create_task(event_listener_task(ws_base, on_refresh=lambda d: root.after(0, update_display, d)))
        loop.run_forever()
        
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
