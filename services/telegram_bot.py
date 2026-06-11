"""Belle's Telegram bot for communicating with relatives."""

import os
import asyncio
import logging
import sqlite3
from pathlib import Path
from datetime import datetime

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
SERVER_URL = "http://localhost:8000"
CAMERA_URL = "http://localhost:8081"

BASE_DIR   = Path(__file__).parent.parent
DB_PATH    = BASE_DIR / "data" / "belle.db"
AUDIOS_DIR = BASE_DIR / "uploads" / "audios"
FOTOS_DIR  = BASE_DIR / "uploads" / "fotos_telegram"

AUDIOS_DIR.mkdir(parents=True, exist_ok=True)
FOTOS_DIR.mkdir(parents=True, exist_ok=True)

BURST_INTERVAL_SEC = 4
BURST_DURATION_SEC = 60


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def register_user(chat_id: int, nombre: str) -> None:
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO telegram_usuarios (chat_id, nombre) VALUES (?, ?)",
        (chat_id, nombre)
    )
    conn.commit()
    conn.close()


def get_all_chat_ids() -> list[int]:
    conn = get_db()
    rows = conn.execute("SELECT chat_id FROM telegram_usuarios WHERE activo = 1").fetchall()
    conn.close()
    return [r["chat_id"] for r in rows]


def get_name(chat_id: int) -> str:
    conn = get_db()
    row  = conn.execute("SELECT nombre FROM telegram_usuarios WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return row["nombre"] if row else "Un familiar"


def enqueue_tts(texto: str) -> None:
    try:
        requests.post(f"{SERVER_URL}/interno/tts", json={"texto": texto}, timeout=3)
    except Exception:
        logger.error("[Telegram] TTS queue error", exc_info=True)


def send_screen_event(evento: dict) -> None:
    try:
        requests.post(f"{SERVER_URL}/interno/evento", json=evento, timeout=3)
        logger.info("[Telegram] event -> %s", evento.get('event'))
    except Exception:
        logger.error("[Telegram] screen event error", exc_info=True)


def _capture_photo_bytes() -> bytes | None:
    try:
        r = requests.get(f"{CAMERA_URL}/foto", timeout=5)
        if r.status_code == 200:
            return r.content
    except Exception:
        logger.error("[Telegram] error capturing photo", exc_info=True)
    return None


def _camera_active() -> bool:
    try:
        r = requests.get(f"{CAMERA_URL}/estado", timeout=2)
        return r.json().get("camara_activa", False)
    except Exception:
        return False


async def _burst_loop(bot, chat_id: int, msg_id: int, chat_data: dict) -> None:
    sent       = 0
    max_photos = BURST_DURATION_SEC // BURST_INTERVAL_SEC

    for _ in range(max_photos):
        if not chat_data.get("rafaga_activa", False):
            break
        loop = asyncio.get_running_loop()
        photo = await loop.run_in_executor(None, _capture_photo_bytes)
        if photo:
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=f"[{datetime.now().strftime('%H:%M:%S')}]",
            )
            sent += 1
        await asyncio.sleep(BURST_INTERVAL_SEC)

    chat_data["rafaga_activa"] = False
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=f"Rafaga terminada - {sent} fotos enviadas.",
        )
    except Exception:
        pass


async def _start_burst(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    if context.chat_data.get("rafaga_activa", False):
        await context.bot.send_message(chat_id=chat_id, text="Ya hay una rafaga en curso.")
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Detener rafaga", callback_data="rafaga_detener"),
    ]])
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"Enviando fotos cada {BURST_INTERVAL_SEC}s durante {BURST_DURATION_SEC}s...",
        reply_markup=keyboard,
    )
    context.chat_data["rafaga_activa"] = True
    asyncio.create_task(
        _burst_loop(context.bot, chat_id, msg.message_id, context.chat_data)
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    nombre  = update.effective_user.first_name or "Familiar"
    register_user(chat_id, nombre)
    await update.message.reply_text(
        f"Hola {nombre}\n\n"
        f"Soy Belle, el asistente de tu familiar.\n\n"
        f"Puedes:\n"
        f"- Escribir un mensaje y Belle lo leera en voz alta\n"
        f"- Enviar una nota de voz y Belle la reproducira\n"
        f"- Enviar una foto con texto y aparecera en la pantalla\n"
        f"- /recordatorio + mensaje para crear un recordatorio\n"
        f"- /foto para ver una captura de la camara ahora mismo\n"
        f"- /rafaga para recibir fotos cada {BURST_INTERVAL_SEC}s durante {BURST_DURATION_SEC}s\n\n"
        f"Ya estas registrado!"
    )
    logger.info("[Telegram] new relative registered: %s (%s)", nombre, chat_id)


async def cmd_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _camera_active():
        await update.message.reply_text(
            "La camara no esta activa ahora mismo.\n"
            "El tracker debe estar en marcha para poder ver a tu familiar."
        )
        return

    await update.message.reply_text("Capturando foto...")
    loop = asyncio.get_running_loop()
    photo = await loop.run_in_executor(None, _capture_photo_bytes)

    if photo is None:
        await update.message.reply_text("No se pudo capturar la foto. Intentalo de nuevo.")
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Otra foto", callback_data="foto_otra"),
        InlineKeyboardButton("Rafaga",    callback_data="rafaga_iniciar"),
    ]])
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=photo,
        caption=datetime.now().strftime('%H:%M:%S'),
        reply_markup=keyboard,
    )


async def cmd_burst(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _camera_active():
        await update.message.reply_text("La camara no esta activa ahora mismo.")
        return
    await _start_burst(update, context, chat_id)


async def on_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    chat_id = update.effective_chat.id

    try:
        await query.answer()
    except Exception:
        pass

    if query.data == "foto_otra":
        await context.bot.send_message(chat_id=chat_id, text="Capturando foto...")
        loop = asyncio.get_running_loop()
        photo = await loop.run_in_executor(None, _capture_photo_bytes)
        if photo:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Otra foto", callback_data="foto_otra"),
                InlineKeyboardButton("Rafaga",    callback_data="rafaga_iniciar"),
            ]])
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=datetime.now().strftime('%H:%M:%S'),
                reply_markup=keyboard,
            )
        else:
            await context.bot.send_message(chat_id=chat_id, text="No se pudo capturar la foto.")

    elif query.data == "rafaga_iniciar":
        if not _camera_active():
            await context.bot.send_message(chat_id=chat_id, text="La camara no esta activa.")
            return
        await _start_burst(update, context, chat_id)

    elif query.data == "rafaga_detener":
        context.chat_data["rafaga_activa"] = False
        try:
            await query.edit_message_text("Rafaga detenida.")
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text="Rafaga detenida.")


async def cmd_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    nombre  = get_name(chat_id)
    parts   = update.message.text.split(" ", 1)

    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "Por favor escribe el recordatorio despues del comando.\n"
            "Ejemplo: /recordatorio Medico el jueves a las 10"
        )
        return

    reminder_text = parts[1].strip()

    try:
        response = requests.post(
            f"{SERVER_URL}/recordatorios/telegram",
            json={
                "texto_original": reminder_text,
                "fecha_hoy":      datetime.now().strftime("%Y-%m-%d"),
                "hora_ahora":     datetime.now().strftime("%H:%M"),
                "creado_por":     nombre,
            },
            timeout=10,
        )
        data = response.json()
        await update.message.reply_text(f"OK - {data.get('mensaje', 'Recordatorio creado')}")
        send_screen_event({
            "event": "show_recordatorio_familiar",
            "data": {
                "texto":      data.get("texto_limpio", reminder_text),
                "creado_por": nombre,
            }
        })
        enqueue_tts(
            f"{nombre} te ha anadido un recordatorio: "
            f"{data.get('texto_limpio', reminder_text)}"
        )
    except Exception:
        logger.error("[Telegram] error creating reminder", exc_info=True)
        await update.message.reply_text("Ha habido un error al crear el recordatorio.")


async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    nombre  = get_name(chat_id)
    texto   = update.message.text
    logger.info("[Telegram] message from %s: %s", nombre, texto)
    enqueue_tts(f"Tienes un mensaje de {nombre}: {texto}")
    send_screen_event({
        "event": "show_mensaje_telegram",
        "data":  {"nombre": nombre, "texto": texto}
    })
    await update.message.reply_text("Mensaje entregado a Belle")


async def on_audio_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    nombre  = get_name(chat_id)
    audio   = update.message.voice
    file    = await context.bot.get_file(audio.file_id)
    path    = AUDIOS_DIR / f"{chat_id}_{audio.file_id}.ogg"
    await file.download_to_drive(path)
    logger.info("[Telegram] audio from %s saved to %s", nombre, path)
    enqueue_tts(f"Tienes un mensaje de voz de {nombre}.")
    try:
        requests.post(
            f"{SERVER_URL}/interno/audio",
            json={"ruta": str(path), "nombre": nombre},
            timeout=3,
        )
    except Exception:
        logger.error("[Telegram] error sending audio", exc_info=True)
    await update.message.reply_text("Audio entregado a Belle")


async def on_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    nombre  = get_name(chat_id)
    caption = update.message.caption or ""
    photo   = update.message.photo[-1]
    file    = await context.bot.get_file(photo.file_id)
    path    = FOTOS_DIR / f"{chat_id}_{photo.file_id}.jpg"
    await file.download_to_drive(path)

    foto_url = f"{SERVER_URL}/fotos_telegram/{path.name}"
    logger.info("[Telegram] photo from %s saved to %s", nombre, path)

    send_screen_event({
        "event": "show_foto_telegram",
        "data":  {"foto_url": foto_url, "nombre": nombre, "caption": caption}
    })
    enqueue_tts(f"{nombre} te ha enviado una foto: {caption}" if caption else f"{nombre} te ha enviado una foto.")
    await update.message.reply_text("Foto enviada a Belle")


async def _check_emergencies(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        r        = requests.get(f"{SERVER_URL}/interno/emergencia/siguiente", timeout=1)
        data     = r.json()
        mensaje  = data.get("mensaje", "")
        foto_url = data.get("foto_url", "")
        if mensaje:
            logger.info("[Telegram] sending emergency to relatives")
            for chat_id in get_all_chat_ids():
                try:
                    await context.bot.send_message(chat_id=chat_id, text=mensaje)
                    if foto_url:
                        await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=foto_url,
                            caption="📷 Foto en el momento de la emergencia",
                        )
                except Exception:
                    logger.error("[Telegram] error sending to %s", chat_id, exc_info=True)
    except Exception:
        pass


def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not found in .env")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("recordatorio", cmd_reminder))
    app.add_handler(CommandHandler("foto",         cmd_photo))
    app.add_handler(CommandHandler("rafaga",       cmd_burst))
    app.add_handler(CallbackQueryHandler(on_button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_message))
    app.add_handler(MessageHandler(filters.VOICE,  on_audio_message))
    app.add_handler(MessageHandler(filters.PHOTO,  on_photo_message))

    app.job_queue.run_repeating(_check_emergencies, interval=2, first=2)

    print("[Telegram] bot started - waiting for messages...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
