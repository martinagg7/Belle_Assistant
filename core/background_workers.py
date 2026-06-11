"""Background polling threads that feed work to Belle's main loop.

Each worker polls one internal endpoint of services.server and either pushes new
work onto a PendingQueue or sets an event for the main loop to react to. All
workers stop when ctx.stop_event is set.
"""
from __future__ import annotations

import os
import logging
import subprocess
import threading
import time

import requests

from config import Config
from core.app_context import AppContext

logger = logging.getLogger(__name__)

SERVER_URL = Config.INTERNAL_SERVER_URL

# Only audios inside uploads/ are played
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADS_ROOT = os.path.realpath(os.path.join(_REPO_ROOT, "uploads"))


def is_safe_audio_path(path: str) -> bool:
    """True if path is an existing file inside the uploads directory.

    Guards the mpv subprocess against playing an arbitrary path that might
    arrive on the internal audio queue.
    """
    if not path:
        return False
    real = os.path.realpath(path)
    return os.path.isfile(real) and (
        real == _UPLOADS_ROOT or real.startswith(_UPLOADS_ROOT + os.sep)
    )


def _save_chat_photo(image_url: str, description: str) -> None:
    """Store a captured photo and its description in Firestore for the web app."""
    try:
        from firebase_client import get_db
        from firebase_admin import firestore as fs
        db = get_db()
        db.collection("messages").add({
            "tipo":      "vision_response",
            "image_url": image_url,
            "texto":     description,
            "origen":    "pi",
            "timestamp": fs.SERVER_TIMESTAMP,
        })
        print("[Vision] photo saved to Firestore")
    except Exception:
        logger.error("Vision: failed to save photo", exc_info=True)


def tts_queue_worker(ctx: AppContext) -> None:
    """Speak any queued TTS text once Belle is neither talking nor listening."""
    while not ctx.stop_event.is_set():
        try:
            if ctx.tts.speaking.is_set() or ctx.stt.listening.is_set():
                time.sleep(0.5)
                continue
            r = requests.get(f"{SERVER_URL}/interno/tts/siguiente", timeout=1)
            texto = r.json().get("texto", "")
            if texto:
                print(f"[TTS queue] {texto}")
                ctx.tts.speak_text(texto)
        except Exception:
            logger.debug("tts_queue_worker poll failed", exc_info=True)
        time.sleep(2)


def vision_worker(ctx: AppContext) -> None:
    """Handle family photo requests: announce, capture a frame and store it."""
    from tools.vision_tool import describe_view_with_photo
    while not ctx.stop_event.is_set():
        try:
            r = requests.get(f"{SERVER_URL}/interno/vision/siguiente", timeout=1)
            data = r.json()
            if data.get("nombre"):
                nombre = data["nombre"]
                print(f"[Vision] photo request from {nombre}")
                try:
                    requests.post(
                        f"{SERVER_URL}/interno/tts",
                        json={"texto": f"{nombre} quiere verte. Voy a hacerte una foto."},
                        timeout=1,
                    )
                except Exception:
                    logger.debug("vision_worker announce failed", exc_info=True)
                image_url, description = describe_view_with_photo()
                _save_chat_photo(image_url, description)
        except Exception:
            logger.debug("vision_worker poll failed", exc_info=True)
        time.sleep(3)


def medication_worker(ctx: AppContext) -> None:
    """Pick up scheduled medication reminders and queue them for the main loop."""
    while not ctx.stop_event.is_set():
        try:
            r = requests.get(f"{SERVER_URL}/interno/medicacion/siguiente", timeout=1)
            data = r.json()
            if data.get("medicamentos"):
                ctx.medication.replace(data)
                ctx.med_event.set()
        except Exception:
            logger.debug("medication_worker poll failed", exc_info=True)
        time.sleep(5)


def chat_worker(ctx: AppContext) -> None:
    """Pick up new family messages and queue them for the main loop."""
    while not ctx.stop_event.is_set():
        try:
            r = requests.get(f"{SERVER_URL}/interno/chat/siguiente", timeout=1)
            data = r.json()
            texto = data.get("texto", "")
            if texto:
                nombre = data.get("nombre", "Familiar")
                ctx.chat.push({"texto": texto, "nombre": nombre})
                ctx.chat_event.set()
        except Exception:
            logger.debug("chat_worker poll failed", exc_info=True)
        time.sleep(3)


def daily_check_worker(ctx: AppContext) -> None:
    """Pick up scheduled daily checks and queue them for the main loop."""
    while not ctx.stop_event.is_set():
        try:
            r = requests.get(f"{SERVER_URL}/interno/daily_check/siguiente", timeout=1)
            data = r.json()
            if data.get("id"):
                ctx.daily_check.push(data)
                ctx.dc_event.set()
        except Exception:
            logger.debug("daily_check_worker poll failed", exc_info=True)
        time.sleep(5)


def presence_worker(ctx: AppContext) -> None:
    """React when the fall-detection module reports nobody is in view."""
    while not ctx.stop_event.is_set():
        try:
            r = requests.get(f"{SERVER_URL}/interno/presencia/siguiente", timeout=1)
            if r.json().get("comprobar"):
                ctx.presence.push(True)
                ctx.pres_event.set()
        except Exception:
            logger.debug("presence_worker poll failed", exc_info=True)
        time.sleep(5)


def tap_worker(ctx: AppContext) -> None:
    """Detect screen taps and set tap_event to interrupt the wake word."""
    while not ctx.stop_event.is_set():
        try:
            r = requests.get(f"{SERVER_URL}/interno/tap/siguiente", timeout=1)
            if r.json().get("tap"):
                print("[Tap] screen activation")
                ctx.tap_event.set()
        except Exception:
            logger.debug("tap_worker poll failed", exc_info=True)
        time.sleep(0.3)


def audio_queue_worker(ctx: AppContext) -> None:
    """Play a family voice note, then queue a reply prompt for the main loop."""
    while not ctx.stop_event.is_set():
        try:
            r = requests.get(f"{SERVER_URL}/interno/audio/siguiente", timeout=1)
            data = r.json()
            ruta = data.get("ruta", "")
            nombre = data.get("nombre", "Familiar")

            # Ignore paths outside uploads/
            if ruta and not is_safe_audio_path(ruta):
                logger.warning("Audio: ignoring unsafe path: %s", ruta)
                ruta = ""

            if ruta:
                print(f"[Audio] playing audio from {nombre}: {ruta}")
                try:
                    requests.post(
                        f"{SERVER_URL}/interno/evento",
                        json={"event": "show_audio_chat", "data": {"nombre": nombre}},
                        timeout=2,
                    )
                except Exception:
                    logger.debug("Audio: show event failed", exc_info=True)

                ret = subprocess.run(
                    ["mpv", "--no-video", "--really-quiet",
                     f"--volume={Config.ELEVENLABS_VOLUME}", ruta],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                if ret.returncode != 0:
                    logger.warning(
                        "Audio: mpv error: %s",
                        ret.stderr.decode(errors="replace").strip(),
                    )

                try:
                    os.remove(ruta)
                    print(f"[Audio] file removed: {ruta}")
                except Exception:
                    logger.debug("Audio: could not remove file", exc_info=True)

                # Back to standby on screen
                try:
                    requests.post(
                        f"{SERVER_URL}/interno/evento",
                        json={"event": "standby"},
                        timeout=2,
                    )
                except Exception:
                    logger.debug("Audio: standby event failed", exc_info=True)

                # Queue a turn to ask if they want to reply
                ctx.chat.push({"texto": None, "nombre": nombre, "tipo": "audio"})
                ctx.chat_event.set()
        except Exception:
            logger.debug("audio_queue_worker poll failed", exc_info=True)
        time.sleep(2)


# Worker startup order
_WORKERS = [
    ("TTS queue", tts_queue_worker),
    ("audio queue", audio_queue_worker),
    ("tap", tap_worker),
    ("medication", medication_worker),
    ("chat", chat_worker),
    ("daily check", daily_check_worker),
    ("presence", presence_worker),
    ("vision", vision_worker),
]


def start_workers(ctx: AppContext) -> None:
    """Start every background worker as a daemon thread."""
    for name, worker in _WORKERS:
        threading.Thread(target=worker, args=(ctx,), daemon=True, name=name).start()
        print(f"[Belle] {name} worker started")
