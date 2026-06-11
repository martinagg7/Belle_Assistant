"""Belle's FastAPI server. Runs alongside main.py.

Responsibilities:
  - REST API: relatives, reminders and Telegram
  - WebSocket: real-time channel Belle -> screen
  - Serve static photos and Telegram photos
  - Scheduler: checks reminders every minute
  - Internal queues: TTS, audio, emergency

Start:
  uvicorn services.server:app --host 0.0.0.0 --port 8000
"""

import json
import base64
import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager, closing
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.scheduler      import start as start_scheduler
from services.cloud_sync import start as start_cloud_sync

logger = logging.getLogger(__name__)

# Silence the polling endpoints in the uvicorn log
logging.getLogger("uvicorn.access").addFilter(
    type("F", (logging.Filter,), {
        "filter": lambda self, r: (
            "/interno/tts/siguiente"         not in r.getMessage() and
            "/interno/audio/siguiente"       not in r.getMessage() and
            "/interno/emergencia/siguiente"  not in r.getMessage() and
            "/interno/chat/siguiente"        not in r.getMessage() and
            "/interno/daily_check/siguiente" not in r.getMessage() and
            "/interno/tap/siguiente"         not in r.getMessage() and
            "/interno/medicacion/siguiente"  not in r.getMessage() and
            "/interno/vision/siguiente"      not in r.getMessage()
        )
    })()
)

BASE_DIR    = Path(__file__).parent.parent
DB_PATH     = BASE_DIR / "data" / "belle.db"
UPLOADS_DIR = BASE_DIR / "uploads" / "familia"
FOTOS_TG    = BASE_DIR / "uploads" / "fotos_telegram"
APP_DIR     = BASE_DIR / "app"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
FOTOS_TG.mkdir(parents=True, exist_ok=True)

_tts_queue:         asyncio.Queue = asyncio.Queue()
_audio_queue:       asyncio.Queue = asyncio.Queue()
_emergency_queue:   asyncio.Queue = asyncio.Queue()
_tap_queue:         asyncio.Queue = asyncio.Queue()
_medication_queue:  asyncio.Queue = asyncio.Queue()
_chat_queue:        asyncio.Queue = asyncio.Queue()
_daily_check_queue: asyncio.Queue = asyncio.Queue()
_vision_queue:      asyncio.Queue = asyncio.Queue()
_presence_queue:    asyncio.Queue = asyncio.Queue()
_last_event:        dict | None   = None


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info("WS: client connected — total: %d", len(self.active))
        if _last_event and _last_event.get("event") != "standby":
            try:
                await ws.send_text(json.dumps(_last_event))
            except Exception:
                pass

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        logger.info("WS: client disconnected — total: %d", len(self.active))

    async def broadcast(self, message: dict):
        global _last_event
        _last_event = message
        data = json.dumps(message)
        alive = []
        for ws in self.active:
            try:
                await ws.send_text(data)
                alive.append(ws)
            except Exception:
                logger.debug("WS: removed dead connection")
        self.active = alive


manager = ConnectionManager()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _elder_name() -> str:
    """Elder's name (for alert messages)."""
    try:
        from tools.profile import get_elder_name
        return get_elder_name() or "tu familiar"
    except Exception:
        return "tu familiar"


def _process_fall(img_bytes: bytes, nombre: str, motivo: str = "caida") -> str:
    """BLOCKING (called in a separate thread): upload the photo to Storage and
    write the alert to Firestore (collection 'alerts', the one the web reads).

    motivo: "caida" or "ausencia". Returns the photo URL (or "" on failure).
    """
    if motivo == "ausencia":
        carpeta = "ausencias"
        texto   = f"{nombre} no responde y la cámara no la ve"
    else:
        carpeta = "caidas"
        texto   = f"Posible caída detectada por la cámara ({nombre})"

    foto_url = ""
    try:
        from tools.vision_tool import _upload_jpeg_to_storage
        if img_bytes:
            foto_url = _upload_jpeg_to_storage(img_bytes, carpeta)
    except Exception:
        logger.error("%s: error uploading photo", motivo, exc_info=True)

    try:
        from firebase_client import get_db as get_firestore
        from firebase_admin import firestore as fs
        db = get_firestore()
        db.collection("alerts").add({
            "tipo":      motivo,
            "texto":     texto,
            "nombre":    nombre,
            "foto_url":  foto_url,
            "timestamp": fs.SERVER_TIMESTAMP,
            "visto":     False,
        })
        logger.info("%s: alert written to Firestore", motivo)
    except Exception:
        logger.error("%s: error writing alert", motivo, exc_info=True)

    return foto_url


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[Server] Belle server started on http://0.0.0.0:8000")
    start_scheduler()
    start_cloud_sync()
    yield
    print("[Server] shutting down...")


app = FastAPI(title="Belle API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text("pong")
            elif msg == "tap_activar":
                await _tap_queue.put(True)
                await manager.broadcast({"event": "show_escuchando"})
    except WebSocketDisconnect:
        manager.disconnect(ws)


@app.post("/interno/evento")
async def send_event(payload: dict):
    await manager.broadcast(payload)
    return {"ok": True}


@app.get("/interno/ultimo-evento")
async def get_last_event():
    if _last_event and _last_event.get("event") != "standby":
        return _last_event
    return {"event": "standby"}


@app.post("/interno/tts")
async def enqueue_tts(payload: dict):
    await _tts_queue.put(payload.get("texto", ""))
    return {"ok": True}


@app.get("/interno/tts/siguiente")
async def next_tts():
    if not _tts_queue.empty():
        return {"texto": await _tts_queue.get()}
    return {"texto": ""}


@app.post("/interno/audio")
async def enqueue_audio(payload: dict):
    await _audio_queue.put({
        "ruta":   payload.get("ruta", ""),
        "nombre": payload.get("nombre", "Un familiar"),
    })
    return {"ok": True}


@app.get("/interno/audio/siguiente")
async def next_audio():
    if not _audio_queue.empty():
        return await _audio_queue.get()
    return {"ruta": "", "nombre": ""}


@app.post("/interno/emergencia")
async def send_emergency(payload: dict):
    await _tts_queue.put(payload.get("texto_voz", "He avisado a tu familia."))
    await _emergency_queue.put({
        "mensaje":  payload.get("mensaje", ""),
        "foto_url": payload.get("foto_url", ""),
    })
    await manager.broadcast({
        "event": "show_emergencia",
        "data":  {
            "mensaje":  payload.get("mensaje", "Emergencia"),
            "foto_url": payload.get("foto_url", ""),
        }
    })
    return {"ok": True}


@app.post("/interno/medicacion")
async def enqueue_medication(payload: dict):
    await _medication_queue.put(payload)
    return {"ok": True}


@app.get("/interno/medicacion/siguiente")
async def next_medication():
    if not _medication_queue.empty():
        return await _medication_queue.get()
    return {"horario": "", "medicamentos": []}


@app.post("/interno/chat")
async def enqueue_chat(payload: dict):
    await _chat_queue.put({
        "texto":  payload.get("texto", ""),
        "nombre": payload.get("nombre", "Familiar"),
    })
    return {"ok": True}


@app.get("/interno/chat/siguiente")
async def next_chat():
    if not _chat_queue.empty():
        return await _chat_queue.get()
    return {"texto": "", "nombre": ""}


@app.post("/interno/daily_check")
async def enqueue_daily_check(payload: dict):
    await _daily_check_queue.put(payload)
    return {"ok": True}


@app.get("/interno/daily_check/siguiente")
async def next_daily_check():
    if not _daily_check_queue.empty():
        return await _daily_check_queue.get()
    return {}


@app.get("/interno/tap/siguiente")
async def next_tap():
    if not _tap_queue.empty():
        await _tap_queue.get()
        return {"tap": True}
    return {"tap": False}


@app.get("/interno/emergencia/siguiente")
async def next_emergency():
    if not _emergency_queue.empty():
        return await _emergency_queue.get()
    return {"mensaje": "", "foto_url": ""}


@app.post("/interno/caida")
async def receive_fall(payload: dict):
    """Fall alert (sent by the watcher) or absence alert (sent by main.py when the
    person does not respond). Those processes have no Firebase, so the alert is
    done here: upload the photo, write the web alert and send Telegram.

    motivo: "caida" (default) or "ausencia".
    """
    motivo   = payload.get("motivo", "caida")
    foto_b64 = payload.get("foto_b64", "")
    img_bytes = b""
    if foto_b64:
        try:
            img_bytes = base64.b64decode(foto_b64)
        except Exception:
            img_bytes = b""

    loop = asyncio.get_event_loop()
    nombre   = await loop.run_in_executor(None, _elder_name)
    # Upload photo and write alert to Firestore, in a separate thread
    foto_url = await loop.run_in_executor(None, _process_fall, img_bytes, nombre, motivo)

    if motivo == "ausencia":
        # The voice is said by main.py, not repeated here
        mensaje_tg = (
            f"⚠️ {nombre} NO RESPONDE\n\n"
            f"La cámara no ve a {nombre} y no contesta cuando Belle le pregunta. "
            f"Por favor, comprueba que está bien."
        )
        texto_pantalla = "La persona no responde"
    else:
        # Voice: Belle warns she will call the family
        await _tts_queue.put("Voy a avisar a tu familia por una posible caída.")
        mensaje_tg = (
            f"🚨 POSIBLE CAÍDA — {nombre}\n\n"
            f"La cámara ha detectado una posible caída. "
            f"Por favor, comprueba que {nombre} está bien."
        )
        texto_pantalla = "Posible caída detectada"

    # Telegram: the bot consumes this queue and sends to all relatives
    await _emergency_queue.put({"mensaje": mensaje_tg, "foto_url": foto_url})

    # Robot screen
    await manager.broadcast({
        "event": "show_emergencia",
        "data":  {"mensaje": texto_pantalla, "foto_url": foto_url},
    })

    return {"ok": True}


@app.post("/interno/presencia")
async def request_presence(payload: dict = None):
    """The watcher reports it has not seen anyone: presence must be checked."""
    await _presence_queue.put(True)
    return {"ok": True}


@app.get("/interno/presencia/siguiente")
async def next_presence():
    if not _presence_queue.empty():
        await _presence_queue.get()
        return {"comprobar": True}
    return {"comprobar": False}


@app.post("/interno/vision")
async def enqueue_vision(payload: dict):
    await _vision_queue.put(payload)
    return {"ok": True}


@app.get("/interno/vision/siguiente")
async def next_vision():
    if not _vision_queue.empty():
        return await _vision_queue.get()
    return {}


@app.post("/recordatorios/telegram")
async def create_reminder_telegram(payload: dict):
    from tools.recordatorios import create_reminder
    from groq import Groq
    from config import Config
    import json as _json

    texto_original = payload.get("texto_original", "")
    fecha_hoy      = payload.get("fecha_hoy", "")
    hora_ahora     = payload.get("hora_ahora", "")
    creado_por     = payload.get("creado_por", "Un familiar")

    prompt_path = BASE_DIR / "prompts" / "reminder_extractor_prompt.md"
    with open(prompt_path, "r", encoding="utf-8") as f:
        extractor_prompt = f.read()

    client   = Groq(api_key=Config.GROQ_API_KEY)
    response = client.chat.completions.create(
        model=Config.GROQ_MODEL,
        messages=[
            {"role": "system", "content": extractor_prompt},
            {"role": "user",   "content": f"Fecha de hoy: {fecha_hoy}\nHora actual: {hora_ahora}\n{texto_original}"}
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
        stream=False,
    )

    data   = _json.loads(response.choices[0].message.content)
    result = create_reminder(
        texto       = data.get("texto", texto_original),
        hora        = data.get("hora", "09:00"),
        tipo        = data.get("tipo", "puntual"),
        fecha       = data.get("fecha", ""),
        dia_mes     = data.get("dia_mes", 0),
        dias_semana = data.get("dias_semana"),
    )

    return {
        "ok":           True,
        "mensaje":      result,
        "texto_limpio": data.get("texto", texto_original),
        "creado_por":   creado_por,
    }


@app.post("/audio/stop")
async def stop_audio_endpoint():
    import subprocess
    subprocess.run(["pkill", "-f", "mpv --no-video"], capture_output=True)
    await manager.broadcast({"event": "standby"})
    return {"ok": True}


@app.get("/familia")
def list_family():
    with closing(get_db()) as conn:
        rows = conn.execute("SELECT * FROM familia WHERE activo = 1 ORDER BY nombre").fetchall()
        return [dict(r) for r in rows]


@app.delete("/familia/{familiar_id}")
def delete_relative(familiar_id: int):
    with closing(get_db()) as conn:
        conn.execute("UPDATE familia SET activo = 0 WHERE id = ?", (familiar_id,))
        conn.commit()
    return {"ok": True, "id": familiar_id}


@app.get("/familia/{familiar_id}")
def get_relative(familiar_id: int):
    with closing(get_db()) as conn:
        row = conn.execute("SELECT * FROM familia WHERE id = ?", (familiar_id,)).fetchone()
    if not row:
        return JSONResponse(status_code=404, content={"error": "no encontrado"})
    return dict(row)


@app.get("/fotos/{filename}")
def serve_photo(filename: str):
    path = UPLOADS_DIR / filename
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": "foto no encontrada"})
    return FileResponse(path)


@app.get("/fotos_telegram/{filename}")
def serve_telegram_photo(filename: str):
    path = FOTOS_TG / filename
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": "foto no encontrada"})
    return FileResponse(path)


@app.get("/recordatorios")
def list_reminders_endpoint():
    with closing(get_db()) as conn:
        rows = conn.execute(
            "SELECT * FROM recordatorios WHERE activo = 1 AND completado = 0 ORDER BY fecha, hora"
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/recordatorios")
def create_reminder_endpoint(payload: dict):
    from tools.recordatorios import create_reminder
    result = create_reminder(
        texto       = payload.get("texto", ""),
        hora        = payload.get("hora", ""),
        tipo        = payload.get("tipo", "puntual"),
        fecha       = payload.get("fecha", ""),
        dia_mes     = payload.get("dia_mes", 0),
        dias_semana = payload.get("dias_semana"),
    )
    return {"ok": True, "mensaje": result}


@app.delete("/recordatorios/{recordatorio_id}")
def delete_reminder(recordatorio_id: int):
    with closing(get_db()) as conn:
        conn.execute("UPDATE recordatorios SET activo = 0 WHERE id = ?", (recordatorio_id,))
        conn.commit()
    return {"ok": True, "id": recordatorio_id}


app.mount("/app", StaticFiles(directory=APP_DIR, html=True), name="app")
