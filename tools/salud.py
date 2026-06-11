"""Belle's health tool. Answers health questions by combining:
- safe profile data (name, age, diseases, allergies),
- relevant fragments from the report and guides (vector store),
- the Groq LLM, guided by prompts/health_prompt.md.

Voice flow: on a symptom, first ask whether to alert the family. If yes, alert;
if no, give the explanation from the RAG.

Terminal test (no voice), from the project root:
    python -m tools.salud "me duelen las rodillas"
"""
import os
import logging
import sqlite3
from datetime import date

from groq import Groq

from config import Config
from rag.config import DB_PATH
from rag.store import VectorStore

logger = logging.getLogger(__name__)

# Optional LEDs, ignored if there is no hardware
try:
    from hardware import leds
except Exception:
    leds = None

PROMPT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "health_prompt.md")

_store  = None
_client = Groq(api_key=Config.GROQ_API_KEY)


def _get_store():
    """Load the vector store once (the first query creates it)."""
    global _store
    if _store is None:
        _store = VectorStore()
    return _store


def preload():
    """Call at Belle startup so the first query does not wait."""
    _get_store()


# Profile data

def _age(fecha_nac):
    if not fecha_nac:
        return None
    try:
        born  = date.fromisoformat(fecha_nac)
        today = date.today()
        return today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    except Exception:
        return None


def _person_data():
    """Read the safe profile fields to fill the {persona} block."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT nombre, fecha_nacimiento, enfermedades, alergias FROM perfil LIMIT 1"
        ).fetchone()
        conn.close()
    except Exception:
        row = None

    name, birth, diseases, allergies = row if row else ("", "", "", "")
    age = _age(birth)

    lines = []
    if name:      lines.append(f"Nombre: {name}")
    if age:       lines.append(f"Edad: {age} años")
    if diseases:  lines.append(f"Enfermedades: {diseases}")
    if allergies: lines.append(f"Alergias: {allergies}")
    return "\n".join(lines) if lines else "Sin datos en el perfil."


# Prompt building

def _build_system_prompt():
    """Load health_prompt.md and fill {persona} with the safe data."""
    with open(PROMPT_PATH, encoding="utf-8") as f:
        template = f.read()
    return template.replace("{persona}", _person_data())


def _build_user_message(pregunta):
    """Build the per-turn blocks: report + guides + question."""
    fragments = _get_store().search_balanced(pregunta)
    report = "\n\n".join(f["texto"] for f in fragments if f["tipo"] == "informe")
    guides = "\n\n".join(f["texto"] for f in fragments if f["tipo"] == "manual")
    return (
        f"<informe>\n{report or 'Sin fragmentos.'}\n</informe>\n\n"
        f"<guias>\n{guides or 'Sin fragmentos.'}\n</guias>\n\n"
        f"<pregunta>\n{pregunta}\n</pregunta>"
    )


# Actions

def answer_health_query(pregunta):
    """Return the health answer written by Groq from the RAG."""
    try:
        resp = _client.chat.completions.create(
            model=Config.GROQ_MODEL,
            messages=[
                {"role": "system", "content": _build_system_prompt()},
                {"role": "user",   "content": _build_user_message(pregunta)},
            ],
            temperature=0.4,
            max_tokens=160,
            stream=False,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        logger.error("salud: error querying Groq", exc_info=True)
        return "Ahora mismo no puedo ayudarte con eso. Coméntaselo a tu médico, por favor."


def notify_family(pregunta):
    """Write a health alert to Firestore for the family to see on the web."""
    try:
        from firebase_client import get_db
        from firebase_admin import firestore as fs
        db = get_db()
        db.collection("messages").add({
            "tipo":      "aviso_salud",
            "texto":     f"Aviso de salud: {pregunta}",
            "origen":    "pi",
            "timestamp": fs.SERVER_TIMESTAMP,
        })
        return True
    except Exception:
        logger.error("salud: error notifying the family", exc_info=True)
        return False


# Voice flow

YES_WORDS = {"sí", "si", "claro", "vale", "avisa", "avísala", "avisale", "por favor"}


def handle_health_query(pregunta, speak, listen):
    """Voice flow for a health query.

    1. ask whether to alert the family,
    2. if yes -> alert the family,
    3. if no (or no answer) -> reply with the RAG.
    'speak' and 'listen' are Belle's TTS and STT functions.
    """
    if leds: leds.speaking()
    speak("Entiendo. ¿Quieres que avise a tu familia?")

    if leds: leds.listening()
    answer = (listen() or "").lower()

    if any(w in answer for w in YES_WORDS):
        if leds: leds.speaking()
        if notify_family(pregunta):
            speak("De acuerdo, he avisado a tu familia.")
        else:
            speak("He intentado avisar a tu familia, pero ha habido un problema.")
        return

    # Does not want to alert, give the RAG info
    if leds: leds.speaking()
    speak(answer_health_query(pregunta))


if __name__ == "__main__":
    import sys
    import time

    pregunta = " ".join(sys.argv[1:]) or "me duelen las rodillas"
    print(f"Pregunta: {pregunta}\n")

    t0 = time.perf_counter()
    preload()
    t1 = time.perf_counter()
    respuesta = answer_health_query(pregunta)
    t2 = time.perf_counter()

    print(respuesta)
    print()
    print(f"[time] initial load (once): {t1 - t0:.2f} s")
    print(f"[time] answer (search + Groq): {t2 - t1:.2f} s")
