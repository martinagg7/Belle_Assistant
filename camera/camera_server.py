#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
"""camera_server.py — the camera "waiter".

Problem it solves: the USB camera can only be opened by ONE process at a time.
Several parts of Belle want a photo (vision_tool "what do you see", telegram_bot..). If each opens /dev/video0 on its own,
they fight and it fails.

Solution: this process is the ONLY one that opens the camera. It keeps it open,
reads photos non-stop and always stores "the last photo". Anyone who wants an
image asks for it over HTTP instead of touching the camera:

    GET http://localhost:8081/foto     returns the last photo as JPEG
    GET http://localhost:8081/salud   -says whether the camera is alive
"""
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2


# Configuration
DEVICE = "/dev/video0"   # robot USB webcam (UVC)
WIDTH  = 640
HEIGHT = 480
PORT   = 8081            # the one vision_tool and telegram_bot already expect


# Shared state
# Last captured frame, protected with a lock
_last_frame = None
_lock = threading.Lock()
_camera_alive = False


def _capture_loop():
    """Background thread: open the camera once and keep reading photos forever,
    leaving each new one in _last_frame. Reopens the camera if it drops.
    """
    global _last_frame, _camera_alive

    while True:
        cap = cv2.VideoCapture(DEVICE, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)

        if not cap.isOpened():
            print(f"[camera] could not open {DEVICE}, retrying in 2s")
            _camera_alive = False
            time.sleep(2)
            continue

        print(f"[camera] opened {DEVICE} at {WIDTH}x{HEIGHT}")
        _camera_alive = True

        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                # Camera failed, reopen it
                print("[camera] read failed, reopening camera")
                _camera_alive = False
                break
            with _lock:
                _last_frame = frame
            # Pause to avoid saturating the CPU
            time.sleep(0.03)   # ~30 reads per second at most

        cap.release()
        time.sleep(1)


def _jpeg_of_last_frame():
    """Take the last photo and encode it to JPEG bytes. None if there is none."""
    with _lock:
        frame = None if _last_frame is None else _last_frame.copy()
    if frame is None:
        return None
    ok, buffer = cv2.imencode(".jpg", frame)
    return buffer.tobytes() if ok else None


class _Handler(BaseHTTPRequestHandler):
    """Handles HTTP requests. Each request runs in its own thread."""

    def do_GET(self):
        if self.path == "/foto":
            jpeg = _jpeg_of_last_frame()
            if jpeg is None:
                self.send_response(503)   # 503 = no photo ready yet
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            self.end_headers()
            self.wfile.write(jpeg)

        elif self.path == "/salud":
            status = b"ok" if _camera_alive else b"sin camara"
            self.send_response(200 if _camera_alive else 503)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(status)

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        # Silence the per-request log
        pass


def main():
    # Continuous capture thread
    threading.Thread(target=_capture_loop, daemon=True).start()

    # HTTP server with multithread support
    server = ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
    print(f"[camera] server listening on http://localhost:{PORT}")
    print("         GET /foto   -> last JPEG photo")
    print("         GET /salud  -> camera status")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[camera] shutting down server")
        server.shutdown()


if __name__ == "__main__":
    main()
