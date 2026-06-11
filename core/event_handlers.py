"""Handlers for each branch of Belle's main loop.

The pending-work handlers (medication, daily check, family message, presence)
pop one item from their queue and return True when they handled it, telling the
main loop to start a fresh iteration. handle_interaction runs the normal
wake-word / conversation / router flow when nothing else is pending.

Spoken strings stay in Spanish on purpose: they are the words Belle says to the
user. Likewise the voice keyword sets below are matched against speech.
"""
from __future__ import annotations

import base64
import logging
import time
from datetime import datetime

import requests

from config import Config
from core.app_context import AppContext
from hardware import leds
from tools.games import play_game
from tools.medicacion import save_log
from tools.profile import get_elder_name

logger = logging.getLogger(__name__)

SERVER_URL = Config.INTERNAL_SERVER_URL

# Voice keywords
MEDICATION_YES_WORDS = {"sí", "si", "tomada", "tomado", "tomé", "la tomé", "me la tomé", "ya", "claro"}
DECLINE_WORDS = {"no", "nada", "dejamos", "déjalo", "no gracias", "no hace falta"}
EMERGENCY_CANCEL_WORDS = {"no", "para", "cancela", "cancelar", "detén", "detener", "stop"}
MATH_GAME_WORDS = {"matemática", "matemáticas", "cálculo", "calcul", "número", "números", "suma", "sumas", "resta", "multiplicar"}


def _show_screen(event: str, data: dict | None = None) -> None:
    """Best-effort POST of a UI event to the internal server."""
    payload: dict = {"event": event}
    if data is not None:
        payload["data"] = data
    try:
        requests.post(f"{SERVER_URL}/interno/evento", json=payload, timeout=2)
    except Exception:
        logger.debug("UI event '%s' failed", event, exc_info=True)


def _save_chat_response(texto: str) -> None:
    """Store the user's spoken reply to a family message in Firestore."""
    try:
        from firebase_client import get_db
        from firebase_admin import firestore as fs
        db = get_db()
        db.collection("messages").add({
            "texto":     texto,
            "origen":    "pi",
            "timestamp": fs.SERVER_TIMESTAMP,
        })
        print(f"[Chat] response saved: {texto}")
    except Exception:
        logger.error("Chat: failed to save response", exc_info=True)


def _report_absence() -> None:
    """Capture a frame and alert the family that the person is not responding.

    Reuses the /interno/caida endpoint with reason "ausencia"; the spoken
    warning is said by the caller, so the endpoint stays silent.
    """
    foto_b64 = ""
    try:
        from tools.vision_tool import _capture_jpeg
        img = _capture_jpeg()
        if img:
            foto_b64 = base64.b64encode(img).decode("ascii")
    except Exception:
        logger.error("Presence: failed to capture photo", exc_info=True)
    try:
        requests.post(
            f"{SERVER_URL}/interno/caida",
            json={"foto_b64": foto_b64, "motivo": "ausencia"},
            timeout=10,
        )
        logger.info("Presence: absence alert sent")
    except Exception:
        logger.error("Presence: failed to send absence alert", exc_info=True)


def _respond(ctx: AppContext, resultado) -> None:
    """Speak the router result, registering it and arming follow-up if it asks."""
    if isinstance(resultado, str):
        leds.speaking()
        ctx.tts.speak_text(resultado)
    elif resultado:
        leds.speaking()
        respuesta = ctx.tts.speak(resultado)
        if respuesta:
            ctx.router.register_response(respuesta)
            if respuesta.strip().endswith("?") and not ctx.conv.is_active():
                ctx.conv.activate("conversacion")


def handle_medication(ctx: AppContext) -> bool:
    """Deliver a medication reminder and record whether each one was taken.

    Returns True when a reminder was handled, so the main loop continues.
    """
    med_data = ctx.medication.pop()
    if not med_data or ctx.conv.is_active():
        return False

    meds = med_data["medicamentos"]
    horario = med_data["horario"]
    logger.info("Medication: reminder at %s for %s", horario, [m["nombre"] for m in meds])

    nombre_mayor = get_elder_name()
    for med in meds:
        _show_screen("show_medicacion", {"nombre": med["nombre"], "dosis": med.get("dosis", "")})

        try:
            aviso = ctx.router._groq_client.generate_medication_reminder(
                nombre_mayor, med["nombre"], med.get("dosis", "")
            )
        except Exception:
            dosis_texto = f", {med['dosis']}" if med.get("dosis") else ""
            aviso = f"{nombre_mayor}, recuerda que debes tomar {med['nombre']}{dosis_texto}."
            logger.warning("Medication: Groq reminder failed, using fallback", exc_info=True)
        leds.speaking()
        ctx.tts.speak_text(aviso)
        time.sleep(5)

        leds.speaking()
        ctx.tts.speak_text("¿Podrías confirmarme por voz que las has tomado?")
        leds.listening()
        confirmacion = ctx.stt.listen()

        if confirmacion:
            try:
                estado = ctx.router._groq_client.classify_medication_confirmation(confirmacion)
            except Exception:
                estado = "tomado" if any(w in confirmacion.lower() for w in MEDICATION_YES_WORDS) else "no_tomado"
                logger.warning("Medication: Groq classify failed, using keyword fallback", exc_info=True)
        else:
            estado = "no_tomado"

        save_log(med, horario, estado)
        logger.info("Medication: %s -> %s", med["nombre"], estado)

        leds.speaking()
        if estado == "tomado":
            ctx.tts.speak_text("Perfecto, queda anotado.")
        else:
            ctx.tts.speak_text("De acuerdo, lo dejo pendiente.")

    _show_screen("standby")
    return True


def handle_daily_check(ctx: AppContext) -> bool:
    """Run a scheduled daily check: ask each question and store the answers.

    Returns True when a check was handled, so the main loop continues.
    """
    check_data = ctx.daily_check.pop()
    if not check_data or ctx.conv.is_active():
        return False

    from tools.daily_check import analyze_response, save_responses
    preguntas = check_data.get("questions", [])
    check_name = check_data.get("name", "check diario")
    check_id = check_data.get("id", "")

    leds.speaking()
    ctx.tts.speak_text(f"Es hora del {check_name}. Voy a hacerte unas preguntas.")
    time.sleep(0.5)

    respuestas = []
    for pregunta in preguntas:
        leds.speaking()
        ctx.tts.speak_text(pregunta)
        leds.listening()
        respuesta_voz = ctx.stt.listen()
        if not respuesta_voz:
            respuesta_voz = "Sin respuesta"

        alerta, razon = analyze_response(pregunta, respuesta_voz)

        respuestas.append({
            "question":    pregunta,
            "answer":      respuesta_voz,
            "timestamp":   datetime.now().strftime("%H:%M"),
            "alert":       alerta,
            "alertReason": razon,
            "seen":        False,
        })

        if alerta:
            ctx.tts.speak_text("Entendido, lo anoto.")
        time.sleep(0.3)

    save_responses(check_id, check_name, respuestas)
    leds.speaking()
    ctx.tts.speak_text("Gracias, he anotado todas tus respuestas.")
    return True


def handle_family_message(ctx: AppContext) -> bool:
    """Read out a family message (or audio prompt) and offer to reply.

    Returns True when a message was handled, so the main loop continues.
    """
    msg_data = ctx.chat.pop()
    if not msg_data or ctx.conv.is_active():
        return False

    msg_familiar = msg_data.get("texto")
    nombre_familiar = msg_data.get("nombre", "Familiar")
    tipo_msg = msg_data.get("tipo", "texto")

    if tipo_msg == "audio":
        logger.info("Chat: audio from %s played, asking whether to reply", nombre_familiar)
        time.sleep(0.4)
        leds.speaking()
        ctx.tts.speak_text(f"¿Quieres responderle a {nombre_familiar}?")
    else:
        logger.info("Chat: message from %s", nombre_familiar)
        _show_screen("show_chat", {"nombre": nombre_familiar})
        leds.speaking()
        ctx.tts.speak_text(f"Tienes un mensaje de {nombre_familiar}: {msg_familiar}")
        time.sleep(0.5)
        leds.speaking()
        ctx.tts.speak_text("¿Le digo algo?")
    leds.listening()
    respuesta_raw = ctx.stt.listen()

    es_negativa = not respuesta_raw or any(w in respuesta_raw.lower() for w in DECLINE_WORDS)

    if not es_negativa and respuesta_raw:
        try:
            respuesta = ctx.router._groq_client.reword_chat_response(respuesta_raw)
        except Exception:
            respuesta = respuesta_raw
            logger.warning("Chat: reformulation failed, sending raw text", exc_info=True)
        _save_chat_response(respuesta)
        leds.speaking()
        ctx.tts.speak_text("Enviado.")
    else:
        leds.speaking()
        ctx.tts.speak_text("De acuerdo.")
    return True


def handle_presence_check(ctx: AppContext) -> bool:
    """Ask if the person is there; alert the family if there is no answer.

    Returns True when a check was handled, so the main loop continues.
    """
    flag = ctx.presence.pop()
    if not flag or ctx.conv.is_active():
        return False

    leds.speaking()
    ctx.tts.speak_text("¿Estás ahí?")
    leds.listening()
    respuesta = ctx.stt.listen()
    if respuesta and respuesta.strip():
        logger.info("Presence: person responded")
    else:
        leds.speaking()
        ctx.tts.speak_text("No te oigo. Voy a avisar a tu familia.")
        _report_absence()
    ctx.pres_event.clear()
    return True


def handle_interaction(ctx: AppContext) -> None:
    """Run the normal turn: listen, route through the active mode, respond."""
    # 1 Listen, with or without wake word depending on the active mode
    if ctx.conv.is_active():
        ctx.tap_event.clear()
        leds.listening()
        texto = ctx.stt.listen()
    else:
        leds.standby()
        ctx.wake.listen(interrupt_event=[
            ctx.tap_event, ctx.chat_event, ctx.med_event, ctx.dc_event, ctx.pres_event,
        ])
        ctx.tap_event.clear()
        ctx.chat_event.clear()
        ctx.med_event.clear()
        ctx.dc_event.clear()
        ctx.pres_event.clear()
        time.sleep(0.1)
        leds.listening()
        pending = (
            ctx.chat.has_items() or ctx.medication.has_items()
            or ctx.daily_check.has_items() or ctx.presence.has_items()
        )
        texto = ctx.stt.listen() if not pending else None

    if not texto or len(texto.strip()) < 2:
        if ctx.conv.is_active():
            ctx.conv.deactivate()
        return

    # 2 Delegate to the active conversation mode
    if ctx.conv.is_active():
        if ctx.conv.reason() == "juego":
            respuesta_texto, continua = ctx.conv.process_game(texto)
            ctx.tts.speak_text(respuesta_texto)
            if not continua:
                _show_screen("standby")
            return
        elif ctx.conv.reason() == "juego_mat":
            from tools.games_math import play_math_game, math_active
            respuesta_texto = play_math_game(texto)
            ctx.tts.speak_text(respuesta_texto)
            if not math_active():
                ctx.conv.deactivate()
                _show_screen("standby")
            return
        elif ctx.conv.reason() == "conversacion":
            ctx.conv.deactivate()
            if ctx.conv.closes_conversation(texto):
                ctx.tts.speak_text("De acuerdo, aquí estoy si me necesitas.")
                return

    # 3 Classify and execute
    resultado = ctx.router.process(texto)

    if resultado in ("MODO_JUEGO", "MODO_JUEGO_MAT"):
        from tools.games_math import play_math_game
        ctx.tts.speak_text("¿Qué te apetece más hoy, el trivial de cultura general o el cálculo mental?")
        eleccion = ctx.stt.listen()
        if eleccion and any(w in eleccion.lower() for w in MATH_GAME_WORDS):
            ctx.conv.activate("juego_mat")
            ctx.tts.speak_text(play_math_game(""))
        else:
            ctx.conv.activate("juego")
            ctx.tts.speak_text(play_game(""))
        return

    if resultado == "CONFIRMAR_EMERGENCIA":
        leds.emergency()
        ctx.tts.speak_text("¿Quieres que avise a tu familia?")
        confirmacion = ctx.stt.listen()
        if confirmacion and any(w in confirmacion.lower() for w in EMERGENCY_CANCEL_WORDS):
            ctx.tts.speak_text("De acuerdo, no aviso a nadie.")
            # Process the rest of the message in case they asked for something else
            resultado2 = ctx.router.process(confirmacion)
            if resultado2 and resultado2 != "CONFIRMAR_EMERGENCIA":
                if isinstance(resultado2, str):
                    ctx.tts.speak_text(resultado2)
                else:
                    respuesta = ctx.tts.speak(resultado2)
                    if respuesta:
                        ctx.router.register_response(respuesta)
                        if respuesta.strip().endswith("?") and not ctx.conv.is_active():
                            ctx.conv.activate("conversacion")
        else:
            # Silence or confirmation, alert the family
            respuesta_emerg = ctx.router.handle_emergency(texto)
            ctx.tts.speak_text(respuesta_emerg)
        return

    if not resultado:
        return

    # 4 Respond
    _respond(ctx, resultado)
