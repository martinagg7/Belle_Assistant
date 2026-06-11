"""Handles communication with the Groq API."""
from __future__ import annotations

import os
import logging

from groq import Groq
from config import Config

logger = logging.getLogger(__name__)


class GroqClient:
    def __init__(self) -> None:
        if not Config.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY not found in .env")

        self._client = Groq(api_key=Config.GROQ_API_KEY)
        self._system_prompt = self._load_system_prompt()
        self._router_prompt = self._load_prompt("router_prompt.md")
        self._history: list[dict] = []
        self._session_text: list[str] = []

    def _load_system_prompt(self) -> str:
        from tools.profile import get_profile
        from datetime import datetime as _dt
        profile = get_profile()
        today_str = _dt.now().strftime("%A %d de %B de %Y")
        # Translate day and month to Spanish
        _days = {"Monday":"lunes","Tuesday":"martes","Wednesday":"miércoles",
                 "Thursday":"jueves","Friday":"viernes","Saturday":"sábado","Sunday":"domingo"}
        _months = {"January":"enero","February":"febrero","March":"marzo","April":"abril",
                   "May":"mayo","June":"junio","July":"julio","August":"agosto",
                   "September":"septiembre","October":"octubre","November":"noviembre","December":"diciembre"}
        for en, es in {**_days, **_months}.items():
            today_str = today_str.replace(en, es)
        return self._load_prompt("system_prompt.md", profile=profile, today=today_str)

    def _load_prompt(self, filename: str, profile: str = "", today: str = "") -> str:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", filename)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Prompt not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return f.read().replace("{perfil}", profile).replace("{fecha_hoy}", today)

    def reply(self, text: str):
        """Normal conversation without internet access."""
        self._record_session_text(text)
        messages = [{"role": "system", "content": self._system_prompt}]
        messages += self._history
        messages += [{"role": "user", "content": text}]

        return self._client.chat.completions.create(
            model=Config.GROQ_MODEL,
            messages=messages,
            temperature=0.7,
            stream=True,
        )

    def reply_with_web(self, text: str):
        """Conversation with real-time internet access.

        Uses groq/compound with integrated web search, only for current info
        that changes day to day.
        """
        self._record_session_text(text)

        system_web = (
            self._system_prompt +
            "\nIMPORTANTE: Para esta consulta DEBES usar la búsqueda web "
            "para obtener información actual y específica. "
            "No respondas desde tu conocimiento general. "
            "Responde en máximo 3 frases cortas y directas. "
            "No incluyas URLs, links ni referencias a páginas web. "
            "No digas 'según' ni cites fuentes. Solo da la información."
        )

        messages = [{"role": "system", "content": system_web}]
        messages += self._history
        messages += [{"role": "user", "content": text}]

        return self._client.chat.completions.create(
            model="groq/compound",
            messages=messages,
            temperature=0.7,
            stream=True,
            search_settings={
                "country": "spain"
            }
        )

    def reply_with_tool(self, text: str, tool_result: str):
        """Call Groq after running a tool."""
        self._record_session_text(text)
        user_message = (
            f"User: {text}\n"
            f"Result: {tool_result}"
        )
        messages = [{"role": "system", "content": self._router_prompt}]
        messages += self._history
        messages += [{"role": "user", "content": user_message}]

        return self._client.chat.completions.create(
            model=Config.GROQ_MODEL,
            messages=messages,
            temperature=0.3,
            stream=True,
        )

    def _record_session_text(self, text: str) -> None:
        # Save user text in the session log
        self._session_text.append(text)
        max_texts = Config.MAX_HISTORY_TURNS * 4
        if len(self._session_text) > max_texts:
            self._session_text = self._session_text[-max_texts:]

    def register_response(self, response: str) -> None:
        last_user = self._session_text[-1] if self._session_text else ""
        self._history.append({"role": "user",      "content": last_user})
        self._history.append({"role": "assistant", "content": response})
        max_messages = Config.MAX_HISTORY_TURNS * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]
        self._save_conversation(last_user, response)

    def _save_conversation(self, user_text: str, belle_text: str) -> None:
        if not user_text or not belle_text:
            return
        try:
            from firebase_client import get_db
            from firebase_admin import firestore as fs
            db = get_db()
            db.collection("conversations").add({
                "user_text":  user_text,
                "belle_text": belle_text,
                "timestamp":  fs.SERVER_TIMESTAMP,
            })
        except Exception:
            logger.error("GroqClient: error saving conversation", exc_info=True)

    def get_session_text(self) -> list[str]:
        return self._session_text

    def refresh_profile(self) -> None:
        """Reload the system prompt with the latest profile data."""
        self._system_prompt = self._load_system_prompt()

    def reword_chat_response(self, text: str) -> str:
        """Turn a dictation like 'tell her that...' into a clean direct message."""
        prompt = f"""El usuario mayor ha dictado esto para enviar a un familiar:
"{text}"

Extrae ÚNICAMENTE el contenido del mensaje que quiere enviar.
NO cambies sus palabras, NO reformules, NO mejores el estilo.
Solo elimina instrucciones como "dile que", "que le digas", "mándale", "contéstale que", etc.
El texto resultante debe sonar exactamente como lo diría él.

Ejemplos:
- "dile que no me he acordado pero que lo llamaré por la tarde" → "No me he acordado pero lo llamaré por la tarde"
- "que estoy bien" → "Estoy bien"
- "sí dile que mañana la llamo" → "Mañana la llamo"
- "no me encuentro muy bien hoy" → "No me encuentro muy bien hoy"

Solo devuelve el mensaje, sin explicaciones ni comillas."""

        resp = self._client.chat.completions.create(
            model=Config.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=80,
        )
        return resp.choices[0].message.content.strip()

    def summarize_emergency(self, text: str) -> str:
        """Summarize in one short sentence what the emergency is about."""
        text = (text or "").strip()
        if not text:
            return ""
        prompt = f"""La persona mayor ha activado una emergencia y ha dicho: "{text}"

Resume en UNA frase muy corta (máximo 8 palabras) qué le ocurre, para avisar a la familia.
Escribe en tercera persona, claro y directo. No saludes ni añadas explicaciones.

Ejemplos:
- "Se encuentra muy mal físicamente"
- "Posible caída en casa"
- "Dolor fuerte en el pecho"
- "Se siente mareada y débil"

Devuelve solo la frase, sin comillas."""

        try:
            resp = self._client.chat.completions.create(
                model=Config.GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=30,
            )
            return resp.choices[0].message.content.strip().strip('"').strip()
        except Exception:
            logger.error("Groq: error summarizing emergency", exc_info=True)
            return ""

    def classify_medication_confirmation(self, text: str) -> str:
        """Return 'tomado' or 'no_tomado' depending on what the user said."""
        prompt = f"""El usuario acaba de responder a la pregunta "¿Has tomado tu medicación?".
Su respuesta fue: "{text}"

Clasifica si ha confirmado que YA la ha tomado en este momento o no.
- Si dice que sí la ha tomado ahora → responde exactamente: tomado
- Si dice que no, que la tomará luego, que no sabe, o cualquier duda → responde exactamente: no_tomado

Responde solo con una palabra: tomado o no_tomado"""

        resp = self._client.chat.completions.create(
            model=Config.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=5,
        )
        result = resp.choices[0].message.content.strip().lower()
        return "tomado" if "tomado" in result and "no_tomado" not in result else "no_tomado"

    def generate_medication_reminder(self, name: str, medication: str, dose: str) -> str:
        """Generate a natural, varied medication reminder."""
        prompt = f"""Genera UN mensaje corto para recordarle a {name} que debe tomar su medicación.
Medicamento: {medication}
Dosis: {dose}

IMPORTANTE: usa exactamente la dosis tal como viene escrita, sin cambiarla ni reformularla.

Ejemplos del tono exacto que debes usar:
- "{name}, recuerda que debes tomar 2 pastillas de Aspirina."
- "{name}, es hora de tomar el Enalapril, 2 pastillas."
- "{name}, no olvides tomar 1 pastilla de Omeprazol ahora."
- "{name}, tienes que tomar 1 Pastilla de Sintron."
- "{name}, recuerda tomar {dose} de {medication}."

Solo devuelve la frase, sin explicaciones ni comillas."""

        resp = self._client.chat.completions.create(
            model=Config.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=60,
        )
        return resp.choices[0].message.content.strip()

    def generate_relative_intro(self, data: str) -> str:
        """Generate a natural introduction for a relative in show_familia."""
        resp = self._client.chat.completions.create(
            model=Config.GROQ_MODEL,
            messages=[
                {"role": "system", "content": (
                    "Transforma los datos de un familiar en 2 frases naturales y breves.\n"
                    "FORMATO OBLIGATORIO:\n"
                    "Frase 1: 'Esta es/Este es tu [relación], [nombre]. Tiene [edad] años y vive en [ciudad].'\n"
                    "Frase 2: '[trabajo si lo hay]. Le gusta [hobbies si los hay]. [recuerdo si lo hay].'\n"
                    "REGLAS:\n"
                    "- Usa SOLO los datos proporcionados. No añadas nada más.\n"
                    "- Si un campo no está, omítelo sin reemplazarlo por nada.\n"
                    "- Habla siempre en segunda persona: 'tu hija', 'tu hijo', nunca 'mi'.\n\n"
                    "EJEMPLO:\n"
                    "Datos: Nombre: Ana, relación: Hija, 38 años, vive en Barcelona, maestra de primaria, le gusta la lectura y el yoga\n"
                    "Respuesta: Esta es tu hija, Ana. Tiene 38 años y vive en Barcelona. Es maestra de primaria y le gusta la lectura y el yoga."
                )},
                {"role": "user", "content": data}
            ],
            temperature=0.1,
            max_tokens=150,
        )
        return resp.choices[0].message.content.strip()

    def clear_history(self) -> None:
        self._history = []
        self._session_text = []
