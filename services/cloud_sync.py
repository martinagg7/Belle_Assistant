"""Syncs reminders and family between Firestore (source of truth) and SQLite (local cache).

At startup: the first on_snapshot brings every existing doc as ADDED.
In real time: later changes (ADDED/MODIFIED/REMOVED) are applied to SQLite.
"""
import os
import json
import logging
import threading

import requests

from config import Config
from firebase_client import get_db as get_firestore
from tools.recordatorios import _get_db as get_sqlite

logger = logging.getLogger(__name__)

SERVER_URL = Config.INTERNAL_SERVER_URL
COLLECTION = "reminders"

_first_time              = True
_first_time_family       = True
_first_time_medication   = True
_first_time_messages     = True
_first_time_daily_checks = True
_sqlite_lock             = threading.Lock()


def _firestore_to_sqlite(doc_id: str, data: dict) -> dict:
    """Convert a Firestore document to the recordatorios table format."""
    fs_type = data.get("type", "once")

    if fs_type == "weekly":
        tipo        = "semanal"
        dias_semana = json.dumps(data.get("days") or [0, 0, 0, 0, 0, 0, 0])
        fecha       = None
        dia_mes     = None
    elif fs_type == "monthly":
        tipo        = "mensual"
        dias_semana = None
        fecha       = None
        dia_mes     = data.get("dayOfMonth")
    else:  # once
        tipo        = "puntual"
        dias_semana = None
        fecha       = data.get("date") or None
        dia_mes     = None

    return {
        "firestore_id": doc_id,
        "texto":        data.get("text", ""),
        "tipo":         tipo,
        "fecha":        fecha,
        "hora":         data.get("time", "09:00"),
        "dia_mes":      dia_mes,
        "dias_semana":  dias_semana,
        "activo":       1 if data.get("active", True) else 0,
        "origin":       data.get("origin") or "web",
    }


def _upsert(conn, r: dict) -> None:
    existing = conn.execute(
        "SELECT id FROM recordatorios WHERE firestore_id = ?", (r["firestore_id"],)
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE recordatorios SET
               texto=?, tipo=?, fecha=?, hora=?, dia_mes=?, dias_semana=?, activo=?
               WHERE firestore_id=?""",
            (r["texto"], r["tipo"], r["fecha"], r["hora"], r["dia_mes"],
             r["dias_semana"], r["activo"], r["firestore_id"]),
        )
    else:
        conn.execute(
            """INSERT INTO recordatorios
               (firestore_id, texto, tipo, fecha, hora, dia_mes, dias_semana, activo, origin, completado)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (r["firestore_id"], r["texto"], r["tipo"], r["fecha"], r["hora"],
             r["dia_mes"], r["dias_semana"], r["activo"], r["origin"]),
        )


def _on_reminders_snapshot(col_snapshot, changes, read_time) -> None:
    """Firestore reminders listener callback — runs in the SDK thread."""
    global _first_time
    with _sqlite_lock:
        conn = get_sqlite()
        try:
            if _first_time:
                conn.execute("DELETE FROM recordatorios WHERE origin = 'web'")
                conn.commit()
                _first_time = False

            for change in changes:
                data   = change.document.to_dict()
                doc_id = change.document.id

                if change.type.name == "REMOVED":
                    conn.execute(
                        "DELETE FROM recordatorios WHERE firestore_id = ?", (doc_id,)
                    )
                elif change.type.name == "MODIFIED":
                    r = _firestore_to_sqlite(doc_id, data)
                    _upsert(conn, r)
                else:
                    if (data.get("origin") or "web") != "pi":
                        r = _firestore_to_sqlite(doc_id, data)
                        _upsert(conn, r)

            conn.commit()
        except Exception:
            logger.error("CloudSync: error in reminders snapshot", exc_info=True)
        finally:
            conn.close()


def _family_upsert(conn, doc_id: str, data: dict) -> None:
    r = {
        "firestore_id":    doc_id,
        "nombre":          data.get("nombre", ""),
        "relacion":        data.get("relacion", ""),
        "telefono":        data.get("telefono", ""),
        "email":           data.get("email", ""),
        "ciudad":          data.get("ciudad", ""),
        "fecha_nacimiento": data.get("fecha_nacimiento", ""),
        "trabajo":         data.get("trabajo", ""),
        "hobbies":         data.get("hobbies", ""),
        "recuerdo":        data.get("recuerdo", ""),
        "notas":           data.get("notas", ""),
        "foto_path":       data.get("foto_url", ""),
        "activo":          1 if data.get("activo", True) else 0,
    }
    existing = conn.execute(
        "SELECT id FROM familia WHERE firestore_id = ?", (doc_id,)
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE familia SET
               nombre=?, relacion=?, telefono=?, email=?, ciudad=?,
               fecha_nacimiento=?, trabajo=?, hobbies=?, recuerdo=?, notas=?,
               foto_path=?, activo=?
               WHERE firestore_id=?""",
            (r["nombre"], r["relacion"], r["telefono"], r["email"], r["ciudad"],
             r["fecha_nacimiento"], r["trabajo"], r["hobbies"], r["recuerdo"],
             r["notas"], r["foto_path"], r["activo"], doc_id),
        )
    else:
        conn.execute(
            """INSERT INTO familia
               (firestore_id, nombre, relacion, telefono, email, ciudad,
                fecha_nacimiento, trabajo, hobbies, recuerdo, notas, foto_path, activo)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, r["nombre"], r["relacion"], r["telefono"], r["email"], r["ciudad"],
             r["fecha_nacimiento"], r["trabajo"], r["hobbies"], r["recuerdo"],
             r["notas"], r["foto_path"], r["activo"]),
        )


def _on_family_snapshot(col_snapshot, changes, read_time) -> None:
    """family_members listener callback — runs in the SDK thread."""
    global _first_time_family
    with _sqlite_lock:
        conn = get_sqlite()
        try:
            if _first_time_family:
                conn.execute("DELETE FROM familia")
                conn.commit()
                _first_time_family = False

            for change in changes:
                data   = change.document.to_dict()
                doc_id = change.document.id

                if change.type.name == "REMOVED":
                    conn.execute("DELETE FROM familia WHERE firestore_id = ?", (doc_id,))
                else:
                    _family_upsert(conn, doc_id, data)

            conn.commit()
            logger.info("CloudSync: family synced (%d changes)", len(changes))
        except Exception:
            logger.error("CloudSync: error in family snapshot", exc_info=True)
        finally:
            conn.close()


def _on_medication_snapshot(col_snapshot, changes, read_time) -> None:
    """Sync medications from Firestore -> SQLite medicamentos."""
    global _first_time_medication
    with _sqlite_lock:
        conn = get_sqlite()
        try:
            if _first_time_medication:
                conn.execute("DELETE FROM medicamentos")
                conn.commit()
                _first_time_medication = False

            for change in changes:
                data   = change.document.to_dict()
                doc_id = change.document.id

                if change.type.name == "REMOVED":
                    conn.execute("DELETE FROM medicamentos WHERE firestore_id = ?", (doc_id,))
                else:
                    existing = conn.execute(
                        "SELECT id FROM medicamentos WHERE firestore_id = ?", (doc_id,)
                    ).fetchone()
                    r = (
                        data.get("nombre", ""),
                        data.get("dosis", ""),
                        data.get("horario", "morning"),
                        data.get("notas", ""),
                        1 if data.get("activo", True) else 0,
                        data.get("hora", ""),
                        doc_id,
                    )
                    if existing:
                        conn.execute(
                            "UPDATE medicamentos SET nombre=?, dosis=?, horario=?, notas=?, activo=?, hora=? WHERE firestore_id=?", r
                        )
                    else:
                        conn.execute(
                            "INSERT INTO medicamentos (nombre, dosis, horario, notas, activo, hora, firestore_id) VALUES (?,?,?,?,?,?,?)", r
                        )

            conn.commit()
            logger.info("CloudSync: medications synced (%d changes)", len(changes))
        except Exception:
            logger.error("CloudSync: error in medication snapshot", exc_info=True)
        finally:
            conn.close()


def _on_messages_snapshot(col_snapshot, changes, read_time) -> None:
    """Detect new family messages (origen='web') and queue them for the Pi."""
    global _first_time_messages
    if _first_time_messages:
        _first_time_messages = False
        return

    for change in changes:
        if change.type.name != "ADDED":
            continue
        data = change.document.to_dict()
        if data.get("origen") != "web":
            continue

        tipo = data.get("tipo", "texto")

        if tipo == "vision_request":
            nombre = data.get("nombre", "Familiar")
            logger.info("CloudSync: photo request from: %s", nombre)
            try:
                requests.post(f"{SERVER_URL}/interno/vision",
                              json={"nombre": nombre}, timeout=1)
            except Exception:
                logger.error("CloudSync: error queueing vision", exc_info=True)

        elif tipo == "audio":
            audio_url = data.get("audio_url", "").strip()
            nombre    = data.get("nombre", "Familiar")
            if not audio_url:
                continue
            logger.info("CloudSync: family audio: %s", nombre)
            try:
                resp = requests.get(audio_url, timeout=10)
                ext  = ".webm" if "webm" in audio_url else ".ogg"
                folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads", "audios")
                os.makedirs(folder, exist_ok=True)
                path = os.path.join(folder, f"chat_{change.document.id}{ext}")
                with open(path, "wb") as f:
                    f.write(resp.content)
                requests.post(f"{SERVER_URL}/interno/audio",
                              json={"ruta": path, "nombre": nombre}, timeout=1)
                logger.info("CloudSync: audio saved and queued: %s", path)
            except Exception:
                logger.error("CloudSync: error processing audio", exc_info=True)

        else:
            texto = data.get("texto", "").strip()
            if not texto:
                continue
            nombre = data.get("nombre", "Familiar")
            logger.info("CloudSync: family message (%s): %s", nombre, texto)
            try:
                requests.post(f"{SERVER_URL}/interno/chat",
                              json={"texto": texto, "nombre": nombre}, timeout=1)
            except Exception:
                logger.error("CloudSync: error queueing chat", exc_info=True)


def _on_daily_checks_snapshot(col_snapshot, changes, read_time) -> None:
    """Sync daily_checks from Firestore -> SQLite."""
    global _first_time_daily_checks
    with _sqlite_lock:
        conn = get_sqlite()
        try:
            if _first_time_daily_checks:
                conn.execute("DELETE FROM daily_checks")
                conn.commit()
                _first_time_daily_checks = False

            for change in changes:
                data   = change.document.to_dict()
                doc_id = change.document.id

                if change.type.name == "REMOVED":
                    conn.execute("DELETE FROM daily_checks WHERE firestore_id = ?", (doc_id,))
                else:
                    preguntas = json.dumps(data.get("questions", []))
                    activo    = 1 if data.get("active", True) else 0
                    existing  = conn.execute(
                        "SELECT id FROM daily_checks WHERE firestore_id = ?", (doc_id,)
                    ).fetchone()
                    if existing:
                        conn.execute(
                            "UPDATE daily_checks SET nombre=?, hora=?, preguntas=?, activo=? WHERE firestore_id=?",
                            (data.get("name", ""), data.get("time", ""), preguntas, activo, doc_id),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO daily_checks (firestore_id, nombre, hora, preguntas, activo) VALUES (?,?,?,?,?)",
                            (doc_id, data.get("name", ""), data.get("time", ""), preguntas, activo),
                        )

            conn.commit()
            logger.info("CloudSync: daily_checks synced (%d changes)", len(changes))
        except Exception:
            logger.error("CloudSync: error in daily_checks snapshot", exc_info=True)
        finally:
            conn.close()


def _on_profile_snapshot(doc_snapshot, changes, read_time) -> None:
    """Sync the profile from Firestore -> SQLite when it changes."""
    try:
        from tools.profile import sync_from_firestore
        sync_from_firestore()
        logger.info("CloudSync: profile synced from Firestore")
    except Exception:
        logger.error("CloudSync: error syncing profile", exc_info=True)


def start() -> None:
    """Start the real-time Firestore listeners (reminders + profile)."""
    db = get_firestore()

    # Reminders
    try:
        db.collection(COLLECTION).on_snapshot(_on_reminders_snapshot)
        logger.info("CloudSync: reminders listener active")
    except Exception:
        logger.error("CloudSync: reminders listener error", exc_info=True)

    # Family
    try:
        db.collection("family_members").on_snapshot(_on_family_snapshot)
        logger.info("CloudSync: family listener active")
    except Exception:
        logger.error("CloudSync: family listener error", exc_info=True)

    # Medications
    try:
        db.collection("medications").on_snapshot(_on_medication_snapshot)
        logger.info("CloudSync: medications listener active")
    except Exception:
        logger.error("CloudSync: medications listener error", exc_info=True)

    # Family messages
    try:
        db.collection("messages").on_snapshot(_on_messages_snapshot)
        logger.info("CloudSync: messages listener active")
    except Exception:
        logger.error("CloudSync: messages listener error", exc_info=True)

    # Daily checks
    try:
        db.collection("daily_checks").on_snapshot(_on_daily_checks_snapshot)
        logger.info("CloudSync: daily_checks listener active")
    except Exception:
        logger.error("CloudSync: daily_checks listener error", exc_info=True)

    # Profile, initial sync + listener
    try:
        from tools.profile import sync_from_firestore
        sync_from_firestore()
        db.collection("elder_profile").document("main").on_snapshot(_on_profile_snapshot)
        logger.info("CloudSync: profile listener active")
    except Exception:
        logger.error("CloudSync: profile listener error", exc_info=True)
