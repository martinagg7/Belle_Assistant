"""Router.

Orchestrates Belle's flow: classifies the user's intent and runs the matching
tool. All communication with the screen goes through _send_event().
"""
from __future__ import annotations

import os
import json
import time
import calendar
import logging
from datetime import datetime, timedelta, date

import requests
from groq import Groq

from config import Config
from core.groq_client import GroqClient
from core.tool_executor import execute
from tools.vision_tool import describe_view

logger = logging.getLogger(__name__)

SERVER_URL = Config.INTERNAL_SERVER_URL


def _period_to_range(period: str) -> dict:
    """Turn a named period into a concrete date range.

    All the arithmetic is done in Python; the LLM only classifies the period.
    The period names and the returned keys/labels are matched downstream, so
    they stay in Spanish.
    """
    today = date.today()

    if period == "hoy":
        return {"desde": str(today), "hasta": str(today), "etiqueta": "hoy"}

    if period == "mañana":
        d = today + timedelta(days=1)
        return {"desde": str(d), "hasta": str(d), "etiqueta": "mañana"}

    if period == "ayer":
        d = today - timedelta(days=1)
        return {"desde": str(d), "hasta": str(d), "etiqueta": "ayer"}

    if period == "esta_semana":
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        return {"desde": str(monday), "hasta": str(sunday), "etiqueta": "esta semana"}

    if period == "semana_pasada":
        monday = today - timedelta(days=today.weekday() + 7)
        sunday = monday + timedelta(days=6)
        return {"desde": str(monday), "hasta": str(sunday), "etiqueta": "la semana pasada"}

    if period == "semana_siguiente":
        monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
        sunday = monday + timedelta(days=6)
        return {"desde": str(monday), "hasta": str(sunday), "etiqueta": "la semana que viene"}

    if period == "este_mes":
        start = today.replace(day=1)
        end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        return {"desde": str(start), "hasta": str(end), "etiqueta": "este mes"}

    if period == "mes_pasado":
        last_of_prev = today.replace(day=1) - timedelta(days=1)
        first_of_prev = last_of_prev.replace(day=1)
        return {"desde": str(first_of_prev), "hasta": str(last_of_prev), "etiqueta": "el mes pasado"}

    if period == "mes_siguiente":
        if today.month == 12:
            start = today.replace(year=today.year + 1, month=1, day=1)
        else:
            start = today.replace(month=today.month + 1, day=1)
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        return {"desde": str(start), "hasta": str(end), "etiqueta": "el mes que viene"}

    # No date filter
    return {"desde": "", "hasta": "", "etiqueta": "todos"}


class Router:
    def __init__(self, tts_callback=None, stt_callback=None) -> None:
        self._groq_client = GroqClient()
        self._client = Groq(api_key=Config.GROQ_API_KEY)
        self._classifier_prompt = self._load_classifier_prompt()
        self._tts_callback = tts_callback
        self._stt_callback = stt_callback

    def _load_classifier_prompt(self) -> str:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "classifier_prompt.md")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _load_reminder_extractor_prompt(self) -> str:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "reminder_extractor_prompt.md")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _send_event(self, event: str, data: dict = None) -> None:
        payload = {"event": event}
        if data:
            payload["data"] = data
        try:
            requests.post(f"{SERVER_URL}/interno/evento", json=payload, timeout=1)
            logger.info("Router: event -> %s", event)
        except Exception:
            logger.warning("Router: could not send event '%s'", event, exc_info=True)

    def _parse_weather(self, result: str) -> dict:
        data = {}
        for part in result.split(", "):
            if "=" in part:
                k, v = part.split("=", 1)
                data[k.strip()] = v.strip()

        condition = data.get("condicion", "").lower()
        if any(p in condition for p in ["sol", "despejado", "soleado", "clear", "sunny"]):
            emoji = "☀️"
        elif any(p in condition for p in ["tormenta", "thunder", "storm"]):
            emoji = "⛈️"
        elif any(p in condition for p in ["lluvia", "rain", "drizzle", "llovizna"]):
            emoji = "🌧️"
        elif any(p in condition for p in ["nieve", "snow"]):
            emoji = "❄️"
        elif any(p in condition for p in ["niebla", "fog", "mist"]):
            emoji = "🌫️"
        elif any(p in condition for p in ["nub", "cloud", "overcast", "parcial"]):
            emoji = "☁️"
        else:
            emoji = "🌤️"

        return {
            "emoji":     emoji,
            "temp":      data.get("temp_actual", data.get("maxima", "--")),
            "condicion": condition,
            "ciudad":    data.get("ciudad", Config.CITY),
            "maxima":    data.get("maxima", ""),
            "minima":    data.get("minima", ""),
        }

    def process(self, text: str):
        response = self._client.chat.completions.create(
            model=Config.GROQ_MODEL,
            messages=[
                {"role": "system", "content": self._classifier_prompt},
                {"role": "user",   "content": text},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            stream=False,
        )
        data = json.loads(response.choices[0].message.content)
        tool_name = data.get("tool", "chat_normal")
        logger.info("Router: tool=%s args=%s", tool_name, data)

        if tool_name == "chat_normal":
            self._send_event("show_thinking")
            return self._groq_client.reply(text)

        elif tool_name == "consulta_salud":
            from tools.salud import handle_health_query
            handle_health_query(text, self._tts_callback, self._stt_callback)
            self._send_event("standby")
            return None

        elif tool_name == "buscar_internet":
            self._send_event("show_thinking")
            if self._tts_callback:
                self._tts_callback("Espera un momento, voy a consultar para darte la información más actualizada.")
            return self._groq_client.reply_with_web(text)

        elif tool_name == "get_time":
            result = execute("get_time", "{}")
            logger.info("Router: result: %s", result)
            if not result.startswith("error"):
                now = datetime.now()
                _days   = {"Monday":"lunes","Tuesday":"martes","Wednesday":"miércoles","Thursday":"jueves","Friday":"viernes","Saturday":"sábado","Sunday":"domingo"}
                _months = {1:"enero",2:"febrero",3:"marzo",4:"abril",5:"mayo",6:"junio",7:"julio",8:"agosto",9:"septiembre",10:"octubre",11:"noviembre",12:"diciembre"}
                self._send_event("show_time", {
                    "hora":  now.strftime("%H:%M"),
                    "fecha": f"{_days[now.strftime('%A')]}, {now.day} de {_months[now.month]}",
                })
            return self._groq_client.reply_with_tool(text, result)

        elif tool_name == "get_weather":
            arguments = {"ciudad": data.get("ciudad", Config.CITY), "dia": data.get("dia", "hoy")}
            result = execute("get_weather", json.dumps(arguments))
            logger.info("Router: result: %s", result)
            if not result.startswith("error"):
                self._send_event("show_weather", self._parse_weather(result))
            return self._groq_client.reply_with_tool(text, result)

        elif tool_name == "get_news":
            arguments = {"categoria": data.get("categoria", "general")}
            result = execute("get_news", json.dumps(arguments))
            logger.info("Router: result: %s...", result[:80])
            if not result.startswith("error"):
                self._send_event("show_news", {"categoria": arguments["categoria"]})
            return self._groq_client.reply_with_tool(text, result)

        elif tool_name == "play_radio":
            tipo = data.get("tipo", "noticias")
            result = execute("play_radio", json.dumps({"tipo": tipo}))
            logger.info("Router: result: %s", result)
            if not result.startswith("error"):
                self._send_event("show_radio", {"tipo": tipo})
            return None

        elif tool_name == "play_youtube":
            query = data.get("query", "música")
            result = execute("play_youtube", json.dumps({"query": query}))
            logger.info("Router: result: %s", result)
            if not result.startswith("error"):
                self._send_event("show_radio", {"tipo": "musica", "query": query})
            return f"Buscando {query}, un momento."

        elif tool_name == "stop_audio":
            result = execute("stop_audio", "{}")
            logger.info("Router: result: %s", result)
            self._send_event("standby")
            return None

        elif tool_name == "create_recordatorio":
            original_text = data.get("texto_original", text)
            extractor_prompt = self._load_reminder_extractor_prompt()
            today_date = datetime.now().strftime("%Y-%m-%d")
            current_time = datetime.now().strftime("%H:%M")

            extractor_response = self._client.chat.completions.create(
                model=Config.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": extractor_prompt},
                    {"role": "user",   "content": f"Fecha de hoy: {today_date}\nHora actual: {current_time}\n{original_text}"},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                stream=False,
            )
            reminder_data = json.loads(extractor_response.choices[0].message.content)
            logger.info("Router: reminder extracted: %s", reminder_data)

            result = execute("create_recordatorio", json.dumps(reminder_data))
            logger.info("Router: result: %s", result)

            if result.startswith("error"):
                return self._groq_client.reply(text)

            self._send_event("show_recordatorio", {
                "texto": reminder_data.get("texto", original_text),
                "hora":  reminder_data.get("hora", ""),
            })
            return self._groq_client.reply_with_tool(text, result)

        elif tool_name == "list_recordatorios":
            period = data.get("periodo", "todos")
            arguments = _period_to_range(period)
            logger.info("Router: period=%s -> range=%s", period, arguments)
            result = execute("list_recordatorios", json.dumps(arguments))
            logger.info("Router: result: %s", result)
            return result

        elif tool_name == "get_familia":
            result = execute("get_familia", "{}")
            if result.startswith("error") or not result.strip():
                return self._groq_client.reply(text)
            instruction = (
                f"{text}\n"
                "[Instrucción: presenta a cada familiar con naturalidad, "
                "una o dos frases por persona: nombre, relación, dónde vive, trabajo y algo que le gusta. "
                "Tono cálido y cercano. Al terminar pregunta: '¿Quieres que te cuente algo más sobre alguno de ellos?']"
            )
            return self._groq_client.reply_with_tool(instruction, result)

        elif tool_name == "get_familiar_detalle":
            searched_name = data.get("nombre", "")
            result = execute("get_familiar_detalle", json.dumps({"nombre": searched_name}))
            if result.startswith("error") or not result.strip():
                return self._groq_client.reply(text)
            return self._groq_client.reply_with_tool(text, result)

        elif tool_name == "show_familia":
            result = execute("show_familia", "{}")
            logger.info("Router: result: %s...", result[:80])

            if result.startswith("error"):
                return self._groq_client.reply(text)

            if result == "sin_familiares":
                return "Todavía no hay familiares registrados."

            relatives = json.loads(result)

            if self._tts_callback:
                self._tts_callback("Voy a presentarte a tu familia.")
                time.sleep(1)

            for f in relatives:
                self._send_event("show_family", f)
                relative_data = (
                    f"Nombre: {f['nombre'].strip()}, relación: {f['relacion']}"
                    + (f", {f['edad']} años" if f.get('edad') else "")
                    + (f", vive en {f['ciudad']}" if f.get('ciudad') else "")
                    + (f", {f['trabajo']}" if f.get('trabajo') else "")
                    + (f", {f['hobbies']}" if f.get('hobbies') else "")
                    + (f", recuerdo: {f['recuerdo']}" if f.get('recuerdo') else "")
                    + (f", {f['notas']}" if f.get('notas') else "")
                )
                try:
                    phrase = self._groq_client.generate_relative_intro(relative_data)
                except Exception:
                    logger.warning("Router: relative presentation fallback", exc_info=True)
                    phrase = f"Este es tu {f['relacion'].lower()}, {f['nombre'].strip()}."
                if self._tts_callback:
                    self._tts_callback(phrase)
                time.sleep(3)

            if self._tts_callback:
                time.sleep(1)
                self._tts_callback("¿Quieres que te cuente algo más sobre alguno de ellos?")

            self._send_event("standby")
            return None

        elif tool_name == "get_medicacion":
            result = execute("get_medicacion", "{}")
            logger.info("Router: result: %s", result)
            if result == "sin_medicacion":
                return "No tienes ningún medicamento registrado."
            if result.startswith("todas_tomadas"):
                total = result.split("=")[1]
                return f"Ya has tomado toda tu medicación de hoy. Los {total} medicamentos están confirmados."
            return self._groq_client.reply_with_tool(text, result)

        elif tool_name == "get_vision":
            self._send_event("show_thinking")
            if self._tts_callback:
                self._tts_callback("Un momento, voy a mirar.")
            description = describe_view()
            return description

        elif tool_name == "emergencia":
            return "CONFIRMAR_EMERGENCIA"

        elif tool_name == "play_game":
            return "MODO_JUEGO"

        elif tool_name == "play_game_math":
            return "MODO_JUEGO_MAT"

        self._send_event("show_thinking")
        result = execute(tool_name, json.dumps({}))
        logger.info("Router: fallback result: %s", result)
        if result.startswith("error"):
            return self._groq_client.reply(text)
        return self._groq_client.reply_with_tool(text, result)

    def handle_emergency(self, user_text: str = "") -> str:
        # Summarize with AI what the person said to alert the family
        summary = self._groq_client.summarize_emergency(user_text)
        logger.info("Router: emergency — summary='%s' text='%s'", summary, user_text)
        result = execute(
            "emergencia",
            json.dumps({"texto_usuario": user_text, "resumen": summary}),
        )
        logger.info("Router: emergency executed: %s", result)
        if result.startswith("error"):
            return "Ha habido un problema al avisar. Inténtalo de nuevo."
        return result

    def refresh_profile(self) -> None:
        """Reload the GroqClient system prompt after a profile update."""
        self._groq_client.refresh_profile()

    def register_response(self, response: str) -> None:
        self._groq_client.register_response(response)
        self._send_event("standby")

    def clear_history(self) -> None:
        self._groq_client.clear_history()
