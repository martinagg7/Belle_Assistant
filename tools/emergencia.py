"""Belle's emergency tool.

When the person says "Belle, ayuda" / "Belle, emergencia":
  1. Belle says a reassuring sentence out loud
  2. Alerts every relative via Telegram
  3. Shows the emergency screen
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime

import requests
from config import Config

logger = logging.getLogger(__name__)

DB_PATH    = Path(__file__).parent.parent / "data" / "belle.db"
SERVER_URL = Config.INTERNAL_SERVER_URL


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_elder_name() -> str:
    conn = _get_db()
    row = conn.execute("SELECT nombre FROM perfil LIMIT 1").fetchone()
    conn.close()
    return row["nombre"] if row and row["nombre"] else "Tu familiar"


def _get_city() -> str:
    conn = _get_db()
    row = conn.execute("SELECT ciudad FROM perfil LIMIT 1").fetchone()
    conn.close()
    return row["ciudad"] if row and row["ciudad"] else "dirección no disponible"


def trigger_emergency(texto_usuario: str = "", resumen: str = "") -> str:
    """Run the emergency protocol and return what Belle should say out loud.

    texto_usuario: what the person literally said.
    resumen: short AI-generated reason for the emergency.
    """
    name = _get_elder_name()
    city = _get_city()
    time_str = datetime.now().strftime("%H:%M")

    # Capture a photo of the moment, does not block on failure
    foto_url = ""
    try:
        from tools.vision_tool import capture_emergency_photo
        foto_url = capture_emergency_photo()
    except Exception:
        logger.error("Emergency: error capturing photo", exc_info=True)

    texto_usuario = (texto_usuario or "").strip()
    resumen = (resumen or "").strip()

    reason_block = ""
    if resumen:
        reason_block += f"⚠️ {resumen}\n"
    if texto_usuario:
        reason_block += f"🗣️ Ha dicho: «{texto_usuario}»\n"
    if reason_block:
        reason_block += "\n"

    telegram_message = (
        f"🚨 EMERGENCIA — {name} necesita ayuda\n\n"
        f"🕐 Hora: {time_str}\n"
        f"📍 Ciudad: {city}\n\n"
        f"{reason_block}"
        f"Por favor contacta con {name} inmediatamente."
    )

    # Send to the server, which forwards it to the Telegram bot
    try:
        requests.post(
            f"{SERVER_URL}/interno/emergencia",
            json={
                "mensaje":   telegram_message,
                "texto_voz": "",   # we handle the voice ourselves
                "foto_url":  foto_url,
            },
            timeout=3,
        )
        logger.info("Emergency: alert sent to relatives")
    except Exception:
        logger.error("Emergency: error sending alert", exc_info=True)

    # Save the alert in Firestore for the web panel
    try:
        from firebase_client import get_db
        from firebase_admin import firestore as fs
        db = get_db()
        db.collection("alerts").add({
            "tipo":      "emergencia",
            "texto":     resumen or texto_usuario or f"{name} ha activado una emergencia",
            "resumen":   resumen,
            "mensaje":   texto_usuario,
            "nombre":    name,
            "foto_url":  foto_url,
            "timestamp": fs.SERVER_TIMESTAMP,
            "visto":     False,
        })
        logger.info("Emergency: alert recorded in Firestore")
    except Exception:
        logger.error("Emergency: error recording alert", exc_info=True)

    return "Estoy avisando a tu familia ahora mismo. No te preocupes, estarán contigo enseguida."
