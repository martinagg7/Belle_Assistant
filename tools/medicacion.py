"""Medication management: reads SQLite and writes logs to Firestore."""

import sqlite3
import logging
from datetime import date
from pathlib import Path

from firebase_admin import firestore as fs
from firebase_client import get_db as get_firestore

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "belle.db"


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_medications_at(hora: str) -> list[dict]:
    """Return active medications whose exact time (HH:MM) matches."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM medicamentos WHERE hora = ? AND activo = 1", (hora,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def has_log_today(hora: str, med_name: str = None) -> bool:
    """Check whether this hour/medication was already logged today."""
    conn = _get_db()
    today = date.today().isoformat()
    if med_name:
        row = conn.execute(
            "SELECT id FROM medication_logs WHERE fecha = ? AND horario = ? AND nombre = ? LIMIT 1",
            (today, hora, med_name),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM medication_logs WHERE fecha = ? AND horario = ? LIMIT 1",
            (today, hora),
        ).fetchone()
    conn.close()
    return row is not None


def get_pending_medication() -> str:
    """Return a spoken summary of today's medication: taken and pending."""
    conn = _get_db()
    today = date.today().isoformat()

    meds = conn.execute(
        "SELECT * FROM medicamentos WHERE activo = 1 ORDER BY hora"
    ).fetchall()
    meds = [dict(m) for m in meds]

    logs = conn.execute(
        "SELECT nombre, estado FROM medication_logs WHERE fecha = ?", (today,)
    ).fetchall()
    conn.close()

    if not meds:
        return "sin_medicacion"

    taken_names = {r["nombre"] for r in logs if r["estado"] == "tomado"}
    pending = [m for m in meds if m["nombre"] not in taken_names]
    num_taken = len(meds) - len(pending)

    if not pending:
        return f"todas_tomadas={len(meds)}"

    def fmt(m):
        hora = m.get("hora") or ""
        d = f' {m["dosis"]}' if m.get("dosis") else ""
        return f'{m["nombre"]}{d} (a las {hora})'

    return (
        f'tomadas={num_taken}/{len(meds)}, '
        f'pendientes={", ".join(fmt(m) for m in pending)}'
    )


def save_log(medicamento: dict, horario: str, estado: str) -> None:
    """Save the medication log to SQLite and Firestore."""
    today = date.today().isoformat()

    conn = _get_db()
    conn.execute(
        """INSERT INTO medication_logs (medicamento_id, nombre, fecha, horario, estado, confirmado_por)
           VALUES (?, ?, ?, ?, ?, 'voz')""",
        (medicamento.get("firestore_id", ""), medicamento["nombre"], today, horario, estado),
    )
    conn.commit()
    conn.close()

    try:
        db = get_firestore()
        db.collection("medication_logs").add({
            "medicamentoId":  medicamento.get("firestore_id", ""),
            "nombre":         medicamento["nombre"],
            "dosis":          medicamento.get("dosis", ""),
            "fecha":          today,
            "horario":        horario,
            "estado":         estado,
            "confirmado_por": "voz",
            "timestamp":      fs.SERVER_TIMESTAMP,
        })
    except Exception:
        logger.error("Medication: Firestore error", exc_info=True)
