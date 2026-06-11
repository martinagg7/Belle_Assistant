"""Daily checks: wellbeing question rounds with per-answer AI analysis."""

import json
import logging
from datetime import date

from groq import Groq
from firebase_admin import firestore as fs
from firebase_client import get_db as get_firestore
from config import Config

logger = logging.getLogger(__name__)


def get_checks_at(hora_actual: str) -> list[dict]:
    """Return active checks whose time matches hora_actual (HH:MM)."""
    try:
        import sqlite3 as _sq
        from pathlib import Path as _Path
        db_path = _Path(__file__).parent.parent / "data" / "belle.db"
        conn = _sq.connect(db_path)
        conn.row_factory = _sq.Row
        rows = conn.execute(
            "SELECT * FROM daily_checks WHERE activo = 1 AND hora = ?", (hora_actual,)
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            result.append({
                "id":        r["firestore_id"],
                "name":      r["nombre"],
                "time":      r["hora"],
                "questions": json.loads(r["preguntas"] or "[]"),
            })
        return result
    except Exception:
        logger.error("DailyCheck: error in get_checks_at", exc_info=True)
        return []


def has_response_today(check_id: str) -> bool:
    """Check whether responses were already saved today for this check."""
    try:
        today = date.today().isoformat()
        db = get_firestore()
        doc = db.collection("check_responses").document(f"{check_id}_{today}").get()
        return doc.exists
    except Exception:
        logger.error("DailyCheck: error in has_response_today", exc_info=True)
        return False


def analyze_response(pregunta: str, respuesta: str) -> tuple[bool, str]:
    """Use Groq to decide whether an answer signals a health concern.

    Returns (alert, reason) with a short Spanish reason.
    """
    try:
        client = Groq(api_key=Config.GROQ_API_KEY)
        system_prompt = (
            "Eres un asistente que analiza respuestas de personas mayores en checks de bienestar. "
            "Responde SOLO en JSON con dos campos: "
            '"alert" (true/false) y "reason" (string en español, máximo 6 palabras, vacío si no hay alerta). '
            "Marca alert=true si la respuesta menciona: dolor, malestar, caída, no ha comido, "
            "no ha dormido, tristeza intensa, confusión, mareo, o cualquier síntoma preocupante. "
            "Marca alert=false si la respuesta es positiva, normal o neutra."
        )
        r = client.chat.completions.create(
            model=Config.GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": f"Pregunta: {pregunta}\nRespuesta: {respuesta}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            stream=False,
        )
        data = json.loads(r.choices[0].message.content)
        return bool(data.get("alert", False)), data.get("reason", "")
    except Exception:
        logger.error("DailyCheck: Groq analysis error", exc_info=True)
        return False, ""


def save_responses(check_id: str, check_name: str, respuestas: list[dict]) -> None:
    """Save responses to Firestore. Doc id: {checkId}_{date}."""
    try:
        today = date.today().isoformat()
        db = get_firestore()
        db.collection("check_responses").document(f"{check_id}_{today}").set({
            "checkId":   check_id,
            "checkName": check_name,
            "date":      today,
            "responses": respuestas,
            "timestamp": fs.SERVER_TIMESTAMP,
        })
        logger.info("DailyCheck: responses saved — %s (%d)", check_name, len(respuestas))
    except Exception:
        logger.error("DailyCheck: error saving responses", exc_info=True)
