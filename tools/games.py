"""Belle's general-knowledge trivia game engine.

Feedback phrases are rotated from code; Groq only evaluates answers and
generates questions.
"""

import os
import json
import time
import logging
import sqlite3

from groq import Groq
from config import Config

logger = logging.getLogger(__name__)

DB_PATH     = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "belle.db")
PROMPT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "games_prompt.md")

# Rotated phrases for variety
CORRECT_PHRASES = [
    "Muy bien.", "Correcto.", "Exacto.", "Así es.",
    "Estupendo.", "Perfecto.", "Bravo.", "Eso es.",
]
WRONG_INTRO_PHRASES = [
    "La correcta era {r}.",
    "Casi, era {r}.",
    "No del todo, era {r}.",
    "La respuesta era {r}.",
    "Era {r}.",
]
ENCOURAGE_PHRASES = [
    "Esta era dura.",
    "Es de las complicadas.",
    "Esa engaña a cualquiera.",
    "Muy difícil esa.",
    "No es fácil saberlo.",
]
TRANSITION_PHRASES = [
    "Seguimos.", "Va la siguiente.", "Ahora algo distinto.",
    "Venga, otra.", "Continuamos.",
]

STOP_PHRASES = [
    "quiero terminar", "terminar", "basta", "para", "paramos",
    "dejamos", "hasta aquí", "ya está", "no quiero seguir",
    "me canso", "estoy cansado", "estoy cansada", "dejarlo",
    "parar", "fin", "acabar", "quiero parar", "quiero dejar",
    "dejar de jugar", "quiero dejar de jugar", "lo dejamos",
    "no quiero jugar", "para el juego", "termina el juego",
]

CONTINUE_PHRASES = [
    "no", "no quiero", "continúa", "continua", "seguimos",
    "sigue", "adelante", "no lo dejamos", "quiero seguir",
]

LEVEL_UP_THRESHOLD   = 7
LEVEL_DOWN_THRESHOLD = -3
REVIEW_INTERVAL      = 5


class GameEngine:

    def __init__(self):
        self._client = Groq(api_key=Config.GROQ_API_KEY)

        self._active          = False
        self._level           = "facil"
        self._points          = 0
        self._level_points    = 0
        self._total_questions = 0
        self._correct         = 0
        self._wrong           = 0
        self._streak          = 0
        self._max_streak      = 0
        self._start_time      = None

        self._current_question = None
        self._current_answer   = None
        self._current_category = None
        self._in_review        = False

        self._waiting_rest_confirmation = False
        self._pending_rest_question     = None
        self._pending_rest_answer       = None
        self._pending_rest_category     = None

        self._used_questions      = []
        self._review_counter      = 0
        self._questions_since_rest = 0

        # Phrase rotation counters
        self._idx_correct    = 0
        self._idx_intro      = 0
        self._idx_encourage  = 0
        self._idx_transition = 0

        self._system_prompt = self._load_prompt()

    def _load_prompt(self) -> str:
        with open(PROMPT_PATH, "r", encoding="utf-8") as f:
            return f.read()

    def _correct_phrase(self) -> str:
        phrase = CORRECT_PHRASES[self._idx_correct % len(CORRECT_PHRASES)]
        self._idx_correct += 1
        return phrase

    def _wrong_phrase(self, correct_answer: str) -> str:
        intro = WRONG_INTRO_PHRASES[self._idx_intro % len(WRONG_INTRO_PHRASES)].format(r=correct_answer)
        encourage = ENCOURAGE_PHRASES[self._idx_encourage % len(ENCOURAGE_PHRASES)]
        self._idx_intro += 1
        self._idx_encourage += 1
        return f"{intro} {encourage}"

    def _transition_phrase(self) -> str:
        phrase = TRANSITION_PHRASES[self._idx_transition % len(TRANSITION_PHRASES)]
        self._idx_transition += 1
        return phrase

    def _build_feedback(self, is_correct: bool, correct_answer: str) -> str:
        base = self._correct_phrase() if is_correct else self._wrong_phrase(correct_answer)
        return f"{base} {self._transition_phrase()}"

    def _wants_to_stop(self, text: str) -> bool:
        return any(p in text.lower().strip() for p in STOP_PHRASES)

    def _wants_to_continue(self, text: str) -> bool:
        return any(p in text.lower().strip() for p in CONTINUE_PHRASES)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(DB_PATH)

    def _save_wrong(self, pregunta: str, respuesta_correcta: str, categoria: str) -> None:
        conn   = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT id, veces_fallada FROM fallos_sesion WHERE pregunta = ?", (pregunta,))
        row = cursor.fetchone()
        if row:
            cursor.execute(
                "UPDATE fallos_sesion SET veces_fallada = ? WHERE id = ?",
                (row[1] + 1, row[0])
            )
        else:
            cursor.execute(
                "INSERT INTO fallos_sesion (pregunta, respuesta_correcta, categoria, veces_fallada) VALUES (?, ?, ?, 1)",
                (pregunta, respuesta_correcta, categoria)
            )
        conn.commit()
        conn.close()

    def _delete_wrong(self, pregunta: str) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM fallos_sesion WHERE pregunta = ?", (pregunta,))
        conn.commit()
        conn.close()

    def _clear_session_wrongs(self) -> None:
        try:
            conn = self._connect()
            conn.execute("DELETE FROM fallos_sesion")
            conn.commit()
            conn.close()
        except Exception:
            logger.error("GameEngine: error clearing fallos_sesion", exc_info=True)

    def _get_review(self) -> dict | None:
        conn   = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT pregunta, respuesta_correcta, categoria FROM fallos_sesion ORDER BY veces_fallada DESC LIMIT 1"
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {"question": row[0], "answer": row[1], "category": row[2]}

    def _is_review_due(self) -> bool:
        if self._review_counter < REVIEW_INTERVAL:
            return False
        conn   = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM fallos_sesion")
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0

    def _save_game(self) -> None:
        duration = round((time.time() - self._start_time) / 60, 1) if self._start_time else 0
        try:
            conn   = self._connect()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT categoria, SUM(veces_fallada) as total FROM fallos_sesion GROUP BY categoria ORDER BY total DESC"
            )
            rows = cursor.fetchall()
            failed_categories = json.dumps({f[0]: f[1] for f in rows}, ensure_ascii=False)
            cursor.execute("""
                INSERT INTO partidas
                    (tipo, nivel_alcanzado, puntos_final, preguntas_total, aciertos, fallos,
                     racha_maxima, duracion_minutos, categorias_falladas)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                'trivial', self._level, self._points, self._total_questions,
                self._correct, self._wrong, self._max_streak,
                duration, failed_categories,
            ))
            conn.execute("DELETE FROM fallos_sesion")
            conn.commit()
            conn.close()
            logger.info("GameEngine: game saved — %dC/%dW duration:%smin", self._correct, self._wrong, duration)
            self._sync_firestore(duration, failed_categories)
        except Exception:
            logger.error("GameEngine: error saving game", exc_info=True)

    def _sync_firestore(self, duration: float, failed_categories: str) -> None:
        try:
            from firebase_client import get_db as get_firestore
            from firebase_admin import firestore as fs
            from datetime import datetime
            db = get_firestore()
            db.collection("game_sessions").add({
                "tipo":             "trivial",
                "fecha":            datetime.now().isoformat(),
                "nivel_alcanzado":  self._level,
                "puntos_final":     self._points,
                "preguntas_total":  self._total_questions,
                "aciertos":         self._correct,
                "fallos":           self._wrong,
                "racha_maxima":     self._max_streak,
                "duracion_minutos": duration,
                "categorias_falladas": failed_categories,
                "timestamp":        fs.SERVER_TIMESTAMP,
            })
            logger.info("GameEngine: game synced to Firestore")
        except Exception:
            logger.error("GameEngine: Firestore sync error", exc_info=True)

    def _reset(self) -> None:
        self._clear_session_wrongs()
        self._active          = False
        self._level           = "facil"
        self._points          = 0
        self._level_points    = 0
        self._total_questions = 0
        self._correct         = 0
        self._wrong           = 0
        self._streak          = 0
        self._max_streak      = 0
        self._start_time      = None
        self._current_question = None
        self._current_answer   = None
        self._current_category = None
        self._in_review        = False
        self._waiting_rest_confirmation = False
        self._pending_rest_question     = None
        self._pending_rest_answer       = None
        self._pending_rest_category     = None
        self._used_questions      = []
        self._review_counter      = 0
        self._questions_since_rest = 0
        self._idx_correct    = 0
        self._idx_intro      = 0
        self._idx_encourage  = 0
        self._idx_transition = 0

    def _call_groq(self, user_answer: str | None, is_review: bool = False) -> dict:
        if user_answer is None:
            message = f"Nivel: {self._level}. Es el primer turno. Genera la primera pregunta."
        else:
            used = "\n".join(f"- {p}" for p in self._used_questions)
            review_context = " Era una pregunta de repaso." if is_review else ""
            message = (
                f"Pregunta anterior: {self._current_question}{review_context}\n"
                f"Respuesta correcta: {self._current_answer}\n"
                f"Respuesta del usuario: {user_answer}\n\n"
                f"Nivel: {self._level}\n\n"
                f"Preguntas ya usadas (no repitas ninguna):\n{used}"
            )

        response = self._client.chat.completions.create(
            model=Config.GROQ_MODEL,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user",   "content": message},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=300,
        )
        return json.loads(response.choices[0].message.content)

    def _update_counters(self, is_correct: bool) -> None:
        if is_correct:
            self._correct      += 1
            self._points       += 1
            self._level_points += 1
            self._streak       += 1
            self._max_streak    = max(self._max_streak, self._streak)
        else:
            self._wrong        += 1
            self._points       -= 1
            self._level_points -= 1
            self._streak        = 0

    def start(self) -> str:
        self._reset()
        self._active = True
        self._start_time = time.time()
        logger.info("GameEngine: game started")

        data = self._call_groq(None)
        self._current_question = data.get("pregunta", "")
        self._current_answer   = data.get("respuesta_correcta", "")
        self._current_category = data.get("categoria", "")
        self._used_questions.append(self._current_question)
        self._total_questions += 1
        self._review_counter  += 1

        return f"Perfecto, empezamos. {self._current_question}"

    def process(self, texto: str) -> str:
        if self._wants_to_stop(texto):
            self._save_game()
            summary = (
                f"Muy bien, lo dejamos aquí. "
                f"Has respondido {self._total_questions} preguntas, "
                f"con {self._correct} aciertos y {self._wrong} fallos. "
                f"Hasta la próxima."
            )
            self._reset()
            return summary

        if self._waiting_rest_confirmation:
            if self._wants_to_continue(texto):
                self._points                    = 0
                self._waiting_rest_confirmation = False
                self._questions_since_rest      = 0
                self._current_question = self._pending_rest_question
                self._current_answer   = self._pending_rest_answer
                self._current_category = self._pending_rest_category
                self._pending_rest_question = None
                self._pending_rest_answer   = None
                self._pending_rest_category = None
                return f"Perfecto, seguimos. {self._current_question}"
            else:
                self._save_game()
                summary = (
                    f"De acuerdo, lo dejamos aquí. "
                    f"Has respondido {self._total_questions} preguntas, "
                    f"con {self._correct} aciertos y {self._wrong} fallos. "
                    f"Hasta la próxima."
                )
                self._reset()
                return summary

        # Spaced review
        if self._is_review_due():
            self._review_counter = 0
            review = self._get_review()
            if review:
                try:
                    data       = self._call_groq(texto, is_review=self._in_review)
                    is_correct = data.get("es_correcto", False)
                except Exception:
                    is_correct = False

                feedback = self._build_feedback(is_correct, self._current_answer)
                self._update_counters(is_correct)

                if is_correct and self._in_review:
                    self._delete_wrong(self._current_question)
                elif not is_correct:
                    self._save_wrong(self._current_question, self._current_answer, self._current_category)

                self._current_question = review["question"]
                self._current_answer   = review["answer"]
                self._current_category = review["category"]
                self._in_review        = True

                return f"{feedback} Antes de seguir, repasamos una que tuviste difícil. {self._current_question}"

        # Normal turn
        try:
            data = self._call_groq(texto, is_review=self._in_review)
        except Exception:
            logger.error("GameEngine: Groq error", exc_info=True)
            return "Vaya, ha habido un problema. ¿Seguimos intentándolo?"

        is_correct   = data.get("es_correcto", False)
        new_question = data.get("pregunta", "")
        new_answer   = data.get("respuesta_correcta", "")
        new_category = data.get("categoria", "")

        # We build the feedback ourselves, not Groq
        feedback = self._build_feedback(is_correct, self._current_answer)

        self._update_counters(is_correct)

        if is_correct and self._in_review:
            self._delete_wrong(self._current_question)
        elif not is_correct:
            self._save_wrong(self._current_question, self._current_answer, self._current_category)

        self._in_review        = False
        self._current_question = new_question
        self._current_answer   = new_answer
        self._current_category = new_category
        if new_question:
            self._used_questions.append(new_question)

        self._total_questions      += 1
        self._review_counter       += 1
        self._questions_since_rest += 1

        parts = [feedback]

        # Scheduled pause every 10 questions
        if (self._questions_since_rest >= 10
                and not self._waiting_rest_confirmation):
            self._waiting_rest_confirmation = True
            self._pending_rest_question     = new_question
            self._pending_rest_answer       = new_answer
            self._pending_rest_category     = new_category
            self._questions_since_rest      = 0
            parts.append("Llevamos ya diez preguntas. ¿Seguimos un poco más o lo dejamos aquí por hoy?")
            return " ".join(p for p in parts if p)

        if self._points <= LEVEL_DOWN_THRESHOLD and not self._waiting_rest_confirmation:
            self._waiting_rest_confirmation = True
            self._pending_rest_question     = new_question
            self._pending_rest_answer       = new_answer
            self._pending_rest_category     = new_category
            parts.append("Llevamos un rato jugando y parece que está costando un poco. ¿Quieres que lo dejemos por hoy?")
        elif self._level_points >= LEVEL_UP_THRESHOLD:
            if self._level == "facil":
                self._level       = "intermedio"
                self._level_points = 0
                parts.append("Lo estás haciendo muy bien. Vamos a subir un poco la dificultad.")
            elif self._level == "intermedio":
                self._level       = "avanzado"
                self._level_points = 0
                parts.append("Estás en racha. Pasamos al nivel avanzado.")
            logger.info("GameEngine: new level: %s", self._level)
            if new_question:
                parts.append(new_question)
        else:
            if new_question:
                parts.append(new_question)

        return " ".join(p for p in parts if p)

    def is_active(self) -> bool:
        return self._active


_engine = GameEngine()


def play_game(texto: str) -> str:
    if not _engine.is_active():
        return _engine.start()
    return _engine.process(texto)


def game_active() -> bool:
    return _engine.is_active()
