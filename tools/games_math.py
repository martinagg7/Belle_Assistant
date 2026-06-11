"""Mental-maths game: additions, subtractions, multiplications and divisions.

No Groq — generation and evaluation are fully local.
"""

import os
import time
import random
import logging
import sqlite3

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "belle.db")

# Spanish word -> number, to parse the spoken answer
_SPANISH_NUMBERS = {
    "cero": 0, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciséis": 16, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18,
    "diecinueve": 19, "veinte": 20, "veintiuno": 21, "veintidós": 22,
    "veintidos": 22, "veintitrés": 23, "veintitres": 23, "veinticuatro": 24,
    "veinticinco": 25, "veintiséis": 26, "veintiseis": 26, "veintisiete": 27,
    "veintiocho": 28, "veintinueve": 29, "treinta": 30,
    "treinta y uno": 31,   "treinta y dos": 32,   "treinta y tres": 33,
    "treinta y cuatro": 34,"treinta y cinco": 35,  "treinta y seis": 36,
    "treinta y siete": 37, "treinta y ocho": 38,   "treinta y nueve": 39,
    "cuarenta": 40,
    "cuarenta y uno": 41,  "cuarenta y dos": 42,   "cuarenta y tres": 43,
    "cuarenta y cuatro": 44,"cuarenta y cinco": 45, "cuarenta y seis": 46,
    "cuarenta y siete": 47,"cuarenta y ocho": 48,  "cuarenta y nueve": 49,
    "cincuenta": 50,
    "cincuenta y uno": 51, "cincuenta y dos": 52,  "cincuenta y tres": 53,
    "cincuenta y cuatro": 54,"cincuenta y cinco": 55,"cincuenta y seis": 56,
    "cincuenta y siete": 57,"cincuenta y ocho": 58, "cincuenta y nueve": 59,
    "sesenta": 60,
    "sesenta y uno": 61,   "sesenta y dos": 62,    "sesenta y tres": 63,
    "sesenta y cuatro": 64,"sesenta y cinco": 65,  "sesenta y seis": 66,
    "sesenta y siete": 67, "sesenta y ocho": 68,   "sesenta y nueve": 69,
    "setenta": 70,
    "setenta y uno": 71,   "setenta y dos": 72,    "setenta y tres": 73,
    "setenta y cuatro": 74,"setenta y cinco": 75,  "setenta y seis": 76,
    "setenta y siete": 77, "setenta y ocho": 78,   "setenta y nueve": 79,
    "ochenta": 80,
    "ochenta y uno": 81,   "ochenta y dos": 82,    "ochenta y tres": 83,
    "ochenta y cuatro": 84,"ochenta y cinco": 85,  "ochenta y seis": 86,
    "ochenta y siete": 87, "ochenta y ocho": 88,   "ochenta y nueve": 89,
    "noventa": 90,
    "noventa y uno": 91,   "noventa y dos": 92,    "noventa y tres": 93,
    "noventa y cuatro": 94,"noventa y cinco": 95,  "noventa y seis": 96,
    "noventa y siete": 97, "noventa y ocho": 98,   "noventa y nueve": 99,
    "cien": 100, "ciento": 100,
}

_SPANISH_NUMBERS_SORTED = sorted(_SPANISH_NUMBERS.items(), key=lambda x: -len(x[0]))


def _parse_number(texto: str) -> int | None:
    texto = texto.lower().strip()
    for token in texto.split():
        try:
            return int(token)
        except ValueError:
            pass
    for phrase, value in _SPANISH_NUMBERS_SORTED:
        if phrase in texto:
            return value
    return None


# Rotated phrases
CORRECT_PHRASES   = ["Muy bien.", "Correcto.", "Exacto.", "Así es.", "Estupendo.", "Perfecto.", "Bravo.", "¡Eso es!"]
WRONG_PHRASES     = ["No era esa.", "Casi.", "No del todo.", "Uy, no.", "Casi, casi."]
ENCOURAGE_PHRASES = ["Seguimos.", "Va la siguiente.", "Otra.", "Continuamos.", "Venga."]
STOP_WORDS = {
    "terminar", "basta", "para", "paramos", "dejamos", "hasta aquí",
    "ya está", "no quiero seguir", "me canso", "estoy cansado",
    "estoy cansada", "parar", "fin", "acabar", "quiero parar",
    "quiero dejar", "lo dejamos", "no quiero jugar",
}
CONTINUE_WORDS = {"sí", "si", "continúa", "continua", "seguimos", "sigue", "adelante", "más", "mas", "quiero seguir", "venga"}

# Difficulty levels, advance with 5 correct answers
LEVELS = [
    {"ops": ["+"],                "max_a": 10, "max_b": 10},  # 0: additions only
    {"ops": ["+", "-"],           "max_a": 20, "max_b": 20},  # 1: additions and subtractions
    {"ops": ["+", "-", "*"],      "max_a": 10, "max_b": 10},  # 2: + multiplications
    {"ops": ["+", "-", "*", "/"], "max_a": 10, "max_b": 10},  # 3: + divisions
]

QUESTIONS_PER_LEVEL = 5


class MathEngine:

    def __init__(self):
        self._active         = False
        self._level          = 0
        self._level_questions = 0
        self._correct        = 0
        self._wrong          = 0
        self._streak         = 0
        self._max_streak     = 0
        self._total          = 0
        self._start_time     = None
        self._answer         = None
        self._waiting_rest   = False
        self._ops_queue      = []   # shuffled cycle of operations
        self._idx_correct  = 0
        self._idx_wrong    = 0
        self._idx_encourage = 0

    def _rotate(self, items, idx_attr) -> str:
        val = items[getattr(self, idx_attr) % len(items)]
        setattr(self, idx_attr, getattr(self, idx_attr) + 1)
        return val

    def _wants_to_stop(self, txt: str) -> bool:
        return any(w in txt.lower().strip() for w in STOP_WORDS)

    def _wants_to_continue(self, txt: str) -> bool:
        return any(w in txt.lower().strip() for w in CONTINUE_WORDS)

    def _generate(self) -> tuple[str, int]:
        cfg = LEVELS[self._level]
        op  = self._next_op()
        ma  = cfg["max_a"]
        mb  = cfg["max_b"]

        if op == "+":
            a, b = random.randint(1, ma), random.randint(1, mb)
            return f"¿Cuánto es {a} más {b}?", a + b
        elif op == "-":
            a, b = random.randint(1, ma), random.randint(1, mb)
            if b > a:
                a, b = b, a
            return f"¿Cuánto es {a} menos {b}?", a - b
        elif op == "*":
            a = random.randint(2, min(ma, 10))
            b = random.randint(2, mb)
            return f"¿Cuánto es {a} por {b}?", a * b
        else:  # "/"
            # Exact division: generate result and divisor, then the dividend
            b      = random.randint(2, min(mb, 10))
            result = random.randint(1, min(ma, 10))
            a      = b * result
            return f"¿Cuánto es {a} dividido entre {b}?", result

    def _save(self) -> None:
        duration = round((time.time() - self._start_time) / 60, 1) if self._start_time else 0
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("""
                INSERT INTO partidas
                    (tipo, nivel_alcanzado, puntos_final, preguntas_total, aciertos, fallos,
                     racha_maxima, duracion_minutos, categorias_falladas)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                'calculo',
                f"calculo_n{self._level}",
                self._correct - self._wrong,
                self._total,
                self._correct,
                self._wrong,
                self._max_streak,
                duration,
                '{"calculo": ' + str(self._wrong) + '}',
            ))
            conn.commit()
            conn.close()
            logger.info("MathEngine: game saved — %dC/%dW %smin", self._correct, self._wrong, duration)
            self._sync_firestore(duration)
        except Exception:
            logger.error("MathEngine: error saving", exc_info=True)

    def _sync_firestore(self, duration: float) -> None:
        try:
            from firebase_client import get_db as get_firestore
            from firebase_admin import firestore as fs
            from datetime import datetime
            db = get_firestore()
            db.collection("game_sessions").add({
                "tipo":             "calculo",
                "fecha":            datetime.now().isoformat(),
                "nivel_alcanzado":  f"calculo_n{self._level}",
                "puntos_final":     self._correct - self._wrong,
                "preguntas_total":  self._total,
                "aciertos":         self._correct,
                "fallos":           self._wrong,
                "racha_maxima":     self._max_streak,
                "duracion_minutos": duration,
                "timestamp":        fs.SERVER_TIMESTAMP,
            })
            logger.info("MathEngine: game synced to Firestore")
        except Exception:
            logger.error("MathEngine: Firestore sync error", exc_info=True)

    def _reset(self) -> None:
        self._active         = False
        self._level          = 0
        self._level_questions = 0
        self._correct        = 0
        self._wrong = self._streak = self._max_streak = self._total = 0
        self._start_time = self._answer = None
        self._waiting_rest = False
        self._ops_queue      = []
        self._idx_correct = self._idx_wrong = self._idx_encourage = 0

    def _next_op(self) -> str:
        """Return operations in shuffled cycles — guarantees real variety."""
        if not self._ops_queue:
            ops = list(LEVELS[self._level]["ops"])
            random.shuffle(ops)
            self._ops_queue = ops
        return self._ops_queue.pop(0)

    def _summary(self) -> str:
        return (
            f"Lo dejamos aquí. Has respondido {self._total} preguntas, "
            f"con {self._correct} aciertos y {self._wrong} fallos. "
            f"¡Buen ejercicio mental!"
        )

    def _level_name(self) -> str:
        names = ["sumas", "sumas y restas", "sumas y restas hasta veinte",
                 "tablas de multiplicar", "tablas con números grandes",
                 "con divisiones", "nivel experto"]
        return names[min(self._level, len(names) - 1)]

    def start(self) -> str:
        self._reset()
        self._active = True
        self._start_time = time.time()
        question, self._answer = self._generate()
        logger.info("MathEngine: game started")
        return f"Vamos con las matemáticas, empezamos por {self._level_name()}. {question}"

    def process(self, texto: str) -> str:
        if self._wants_to_stop(texto):
            self._save()
            summary = self._summary()
            self._reset()
            return summary

        # Scheduled pause every 10 questions
        if self._waiting_rest:
            if self._wants_to_continue(texto):
                self._waiting_rest = False
                question, self._answer = self._generate()
                return f"¡Perfecto, seguimos! {question}"
            else:
                self._save()
                summary = self._summary()
                self._reset()
                return summary

        number     = _parse_number(texto)
        is_correct = number is not None and number == self._answer

        if is_correct:
            self._correct   += 1
            self._streak    += 1
            self._max_streak = max(self._max_streak, self._streak)
            feedback         = self._rotate(CORRECT_PHRASES, "_idx_correct")
        else:
            self._wrong  += 1
            self._streak  = 0
            wrong_txt     = self._rotate(WRONG_PHRASES, "_idx_wrong")
            encourage_txt = self._rotate(ENCOURAGE_PHRASES, "_idx_encourage")
            if number is None:
                feedback = f"No te he entendido. La respuesta era {self._answer}. {encourage_txt}"
            else:
                feedback = f"{wrong_txt} Era {self._answer}. {encourage_txt}"

        self._total           += 1
        self._level_questions += 1

        # Pause every 10 questions
        if self._total % 10 == 0:
            self._waiting_rest = True
            return f"{feedback} Llevamos ya {self._total} preguntas. ¿Seguimos un poco más o descansamos?"

        # Level up every 5 questions
        if self._level_questions >= QUESTIONS_PER_LEVEL and self._level < len(LEVELS) - 1:
            self._level           += 1
            self._level_questions  = 0
            self._ops_queue        = []   # fresh cycle with the new operations
            announce = {
                1: "Muy bien, ahora añadimos las restas.",
                2: "Perfecto, ahora añadimos las multiplicaciones.",
                3: "Estupendo, ahora añadimos también las divisiones.",
            }.get(self._level, "Subimos de nivel.")
            question, self._answer = self._generate()
            return f"{feedback} {announce} {question}"

        question, self._answer = self._generate()
        return f"{feedback} {question}"

    def is_active(self) -> bool:
        return self._active


_engine_math = MathEngine()


def play_math_game(texto: str) -> str:
    if not _engine_math.is_active():
        return _engine_math.start()
    return _engine_math.process(texto)


def math_active() -> bool:
    return _engine_math.is_active()
