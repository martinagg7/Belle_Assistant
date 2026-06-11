#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
"""vigilancia.py — Fall-watch rounds, integrated into Belle.

Every INTERVAL_MIN minutes it does a sweep: for each PAN level it scans the TILT
side to side. At each point it looks with YOLO. If it sees a fall posture (box
WIDER than tall AND its center in the LOWER part), it asks GEMINI to confirm. If
Gemini confirms, it draws a RED rectangle on the photo and alerts Belle (which
forwards it to the web, Telegram and says it out loud).

ARCHITECTURE:
    - Does NOT open the camera: it asks the waiter (camera_server :8081) for
      photos. So the waiter MUST be running.
    - Moves the servos for the sweep.
    - Runs with the camera venv (has YOLO and, after 'pip install', Gemini).

    pan  = HEIGHT  (up/down)
    tilt = SIDEWAYS (left/right)

Needs in venv_camera (once):
    pip install google-genai python-dotenv

Starts with Belle from start.sh. To test by hand (with the waiter running):
    source venv_camera/bin/activate
    python vigilancia.py
"""
import os
import sys
import time
import base64

# Add the project root to the path so imports work
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import cv2
import numpy as np
import requests

from ultralytics import YOLO
from hardware import servos


# Gemini
_gemini_client = None
try:
    from dotenv import load_dotenv
    load_dotenv()
    from google import genai
    from google.genai import types
    _api_key = os.getenv("GEMINI_API_KEY", "")
    if _api_key:
        _gemini_client = genai.Client(api_key=_api_key)
    else:
        print("[Gemini] missing GEMINI_API_KEY in .env -> cannot confirm")
except Exception as e:
    print(f"[Gemini] not available ({e}) -> cannot confirm")

FALL_PROMPT = (
    "Mira la imagen. ¿Hay una persona caida o tumbada en el suelo? "
    "Responde SI si ves a alguien caido/tumbado en el suelo, o NO si esta de pie, "
    "sentada, agachada o no hay nadie caido. Contesta solo una palabra: SI o NO."
)


# Configuration
PAN_LEVELS  = [20, 60, 100]        # HEIGHT levels (pan)
TILT_STOPS  = [10, 40, 70, 100]    # horizontal sweep points (tilt)
MODEL_PATH  = os.path.join(_ROOT, "camera", "modelos", "yolov8n-pose_ncnn_model")

CAMERA_SERVER_URL = "http://localhost:8081"   # where we ask for photos
BELLE_URL         = "http://localhost:8000"   # where we report a fall

STEP_DEGREES = 3
STEP_PAUSE   = 0.02
PHOTO_PAUSE  = 0.4

MIN_CONF        = 0.5
FLOOR_RATIO     = 1.0    # width > height*FLOOR_RATIO -> horizontal
LOWER_THRESHOLD = 0.5    # the person's center must be below this line (0..1)
MAX_GEMINI      = 5      # cap on Gemini calls per round

INTERVAL_MIN = 15        # how many minutes between rounds

REST_POSITION = (60, 30)      # rest position (height, sideways)


_pos = {"alt": 60, "horiz": 30}


def move_smooth(alt_obj, horiz_obj):
    """Move the camera step by step to (alt_obj, horiz_obj), without jumps."""
    alt0, horiz0 = _pos["alt"], _pos["horiz"]
    distance = max(abs(alt_obj - alt0), abs(horiz_obj - horiz0))
    steps = max(1, distance // STEP_DEGREES)
    for i in range(1, steps + 1):
        alt   = round(alt0   + (alt_obj   - alt0)   * i / steps)
        horiz = round(horiz0 + (horiz_obj - horiz0) * i / steps)
        servos.look_at(alt, horiz)
        time.sleep(STEP_PAUSE)
    servos.look_at(alt_obj, horiz_obj)
    _pos["alt"], _pos["horiz"] = alt_obj, horiz_obj


def _request_photo():
    """Ask the waiter for the current photo and return it as an image, or None."""
    try:
        r = requests.get(f"{CAMERA_SERVER_URL}/foto", timeout=4)
        if r.status_code == 200 and r.content:
            arr = np.frombuffer(r.content, dtype=np.uint8)
            return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception as e:
        print(f"[vigilancia] could not request a photo from the waiter: {e}")
    return None


def _analyze(model, frame):
    """Return (person_count, candidate_box).

    person_count  = how many people are visible
    candidate_box = (x1,y1,x2,y2) of a person in a fall posture, or None
    """
    res = model(frame, verbose=False)[0]
    if res.boxes is None:
        return 0, None

    frame_height = frame.shape[0]
    lower_line   = frame_height * LOWER_THRESHOLD

    n = 0
    candidate = None
    for (x1, y1, x2, y2), conf in zip(res.boxes.xyxy.tolist(), res.boxes.conf.tolist()):
        if conf < MIN_CONF:
            continue
        n += 1
        width  = x2 - x1
        height = y2 - y1
        horizontal = width > height * FLOOR_RATIO
        center_y   = (y1 + y2) / 2
        below      = center_y > lower_line
        if horizontal and below:
            candidate = (int(x1), int(y1), int(x2), int(y2))
    return n, candidate


def _confirm_with_gemini(jpeg_bytes):
    """Ask Gemini whether there is a fallen person. True if confirmed."""
    if _gemini_client is None:
        print("[Gemini] not configured -> not confirmed")
        return False
    try:
        resp = _gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                FALL_PROMPT,
                types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"),
            ],
        )
        text = resp.text.strip().upper()
        print(f"[Gemini] reply: {text}")
        return text.startswith("SI") or text.startswith("SÍ")
    except Exception as e:
        print(f"[Gemini] error: {e}")
        return False


def _report_fall(frame_with_box):
    """Send the photo (with the red rectangle) to Belle so it alerts."""
    ok, buffer = cv2.imencode(".jpg", frame_with_box)
    if not ok:
        return
    foto_b64 = base64.b64encode(buffer.tobytes()).decode("ascii")
    try:
        requests.post(f"{BELLE_URL}/interno/caida",
                      json={"foto_b64": foto_b64}, timeout=15)
        print("  fall alert sent to Belle (web + Telegram + voice)")
    except Exception as e:
        print(f"  error alerting Belle: {e}")


def do_round(model):
    """One full sweep. Returns (fall, gemini_calls, person_seen)."""
    calls = 0
    fall = None
    person_seen = False

    for i, alt in enumerate(PAN_LEVELS):
        stops = TILT_STOPS if i % 2 == 0 else list(reversed(TILT_STOPS))

        for horiz in stops:
            move_smooth(alt, horiz)
            time.sleep(PHOTO_PAUSE)

            frame = _request_photo()
            if frame is None:
                print(f"pan={alt:<3} tilt={horiz:<3} -> no photo (waiter down?)")
                continue

            n, candidate = _analyze(model, frame)
            if n > 0:
                person_seen = True

            if candidate is None:
                continue   # no fall posture at this point, keep going

            if calls >= MAX_GEMINI:
                print(f"  cap of {MAX_GEMINI} Gemini calls reached, asking no more")
                continue

            ok, buffer = cv2.imencode(".jpg", frame)
            if not ok:
                continue
            calls += 1
            print(f"  suspected fall -> asking Gemini ({calls}/{MAX_GEMINI})")

            if _confirm_with_gemini(buffer.tobytes()):
                # Draw a red rectangle over the person and alert
                x1, y1, x2, y2 = candidate
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 3)
                _report_fall(frame)
                fall = (alt, horiz)
                print("  >>> FALL CONFIRMED")
                break

        if fall is not None:
            break

    return fall, calls, person_seen


def main():
    print("Loading YOLO-pose model...")
    model = YOLO(MODEL_PATH)
    print("  model loaded")
    print(f"[vigilancia] active: one round every {INTERVAL_MIN} min. Ctrl+C to stop.")

    try:
        while True:
            t0 = time.time()
            fall, calls, person_seen = do_round(model)
            move_smooth(*REST_POSITION)   # back to rest when done

            secs = time.time() - t0
            if fall is not None:
                print(f"ROUND: FALL confirmed at {fall}, alert sent to Belle. ({secs:.0f}s)")
            elif person_seen:
                print(f"ROUND: person detected, no falls. ({secs:.0f}s)")
            else:
                # Nobody seen, ask Belle to check
                print(f"ROUND: nobody seen -> check presence. ({secs:.0f}s)")
                try:
                    requests.post(f"{BELLE_URL}/interno/presencia", json={}, timeout=5)
                except Exception as e:
                    print(f"  error requesting presence check: {e}")

            time.sleep(INTERVAL_MIN * 60)

    except KeyboardInterrupt:
        print("\n[vigilancia] stopped")
    finally:
        servos.close()


if __name__ == "__main__":
    main()
