"""Belle's reminder management."""

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path(__file__).parent.parent / "data" / "belle.db"

MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

WEEKDAYS = [
    "lunes", "martes", "miércoles", "jueves",
    "viernes", "sábado", "domingo",
]


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _natural_date(fecha_str: str) -> str:
    """Turn '2026-04-01' into 'miércoles 1 de abril'."""
    try:
        d = datetime.strptime(fecha_str, "%Y-%m-%d")
        weekday = WEEKDAYS[d.weekday()]
        day = d.day
        month = MONTHS[d.month - 1]
        return f"{weekday} {day} de {month}"
    except Exception:
        return fecha_str


def _natural_time(hora_str: str) -> str:
    """Turn '17:00' into 'las 5 de la tarde'."""
    try:
        h, m = map(int, hora_str.split(":"))
        minutes = f" y {m}" if m > 0 else ""
        if h == 0:
            return f"las 12{minutes} de la noche"
        elif h < 12:
            return f"las {h}{minutes} de la mañana"
        elif h == 12:
            return f"las 12{minutes} del mediodía"
        elif h < 21:
            return f"las {h - 12}{minutes} de la tarde"
        else:
            return f"las {h - 12}{minutes} de la noche"
    except Exception:
        return hora_str


def _write_to_firestore(texto: str, hora: str, tipo: str, fecha: str,
                        dia_mes: int, dias_semana: list) -> str | None:
    """Write the reminder to Firestore with origin='pi'. Return the doc id or None."""
    try:
        from firebase_client import get_db
        from firebase_admin import firestore as _fs

        if tipo == "semanal":
            payload = {"type": "weekly", "days": dias_semana or [0]*7}
        elif tipo == "mensual":
            payload = {"type": "monthly", "dayOfMonth": dia_mes or 1}
        else:
            payload = {"type": "once", "date": fecha or ""}

        payload.update({
            "text":      texto,
            "time":      hora,
            "active":    True,
            "origin":    "pi",
            "createdAt": _fs.SERVER_TIMESTAMP,
        })

        _, ref = get_db().collection("reminders").add(payload)
        return ref.id
    except Exception as e:
        print(f"[Recordatorios] error writing to Firestore: {e}")
        return None


def create_reminder(
    texto:       str,
    hora:        str,
    tipo:        str        = "puntual",
    fecha:       str        = "",
    dia_mes:     int        = 0,
    dias_semana: list | None = None,
) -> str:
    # Normalize: daily -> weekly every day
    if tipo == "diario":
        tipo        = "semanal"
        dias_semana = dias_semana or [1, 1, 1, 1, 1, 1, 1]

    dias_json = json.dumps(dias_semana) if dias_semana is not None else None

    conn   = _get_db()
    cursor = conn.execute(
        """INSERT INTO recordatorios (texto, tipo, fecha, hora, dia_mes, dias_semana, origin)
           VALUES (?, ?, ?, ?, ?, ?, 'pi')""",
        (texto, tipo, fecha or None, hora, dia_mes or None, dias_json),
    )
    sq_id = cursor.lastrowid
    conn.commit()

    # Sync to Firestore
    firestore_id = _write_to_firestore(texto, hora, tipo, fecha, dia_mes, dias_semana or [])
    if firestore_id:
        conn.execute("UPDATE recordatorios SET firestore_id = ? WHERE id = ?", (firestore_id, sq_id))
        conn.commit()
    conn.close()

    hora_natural = _natural_time(hora)

    if tipo == "semanal":
        if not dias_semana or all(d == 1 for d in dias_semana):
            return f"He añadido el recordatorio. Cada día a {hora_natural} te avisaré de que {texto.lower()}."
        active_days = [WEEKDAYS[i] for i, v in enumerate(dias_semana) if v]
        days_str = ", ".join(active_days)
        return f"He añadido el recordatorio. Cada {days_str} a {hora_natural} te avisaré de que {texto.lower()}."
    elif tipo == "mensual":
        return f"He añadido el recordatorio. El día {dia_mes} de cada mes a {hora_natural} te avisaré de que {texto.lower()}."
    else:
        fecha_natural = _natural_date(fecha)
        return f"He añadido el recordatorio. El {fecha_natural} a {hora_natural} te avisaré de que {texto.lower()}."


def mark_past_as_completed() -> None:
    """Mark one-off reminders whose date has already passed as completed."""
    hoy = datetime.now().strftime("%Y-%m-%d")
    conn = _get_db()
    conn.execute(
        "UPDATE recordatorios SET completado = 1 "
        "WHERE tipo = 'puntual' AND fecha < ? AND completado = 0 AND activo = 1",
        (hoy,),
    )
    conn.commit()
    conn.close()


def _weekly_in_range(dias_semana_json: str, desde: str, hasta: str) -> bool:
    """True if any active day of the weekly pattern falls within [desde, hasta]."""
    try:
        days  = json.loads(dias_semana_json or "[0,0,0,0,0,0,0]")
        start = datetime.strptime(desde, "%Y-%m-%d").date()
        end   = datetime.strptime(hasta,  "%Y-%m-%d").date()
        delta = (end - start).days
        for i in range(min(delta + 1, 7)):
            d = start + timedelta(days=i)
            if days[d.weekday()]:  # weekday() = 0 monday ... 6 sunday
                return True
        return False
    except Exception:
        return False


def _monthly_in_range(dia_mes: int, desde: str, hasta: str) -> bool:
    """True if dia_mes falls within [desde, hasta]."""
    import calendar as _cal
    try:
        start = datetime.strptime(desde, "%Y-%m-%d").date()
        end   = datetime.strptime(hasta, "%Y-%m-%d").date()
    except ValueError:
        return False

    current = start.replace(day=1)
    while current <= end:
        last = _cal.monthrange(current.year, current.month)[1]
        if dia_mes <= last:
            from datetime import date as _date
            candidate = _date(current.year, current.month, dia_mes)
            if start <= candidate <= end:
                return True
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return False


def list_reminders(desde: str = "", hasta: str = "", etiqueta: str = "todos") -> str:
    """List reminders.

    desde/hasta: dates in YYYY-MM-DD. Empty -> all pending.
    etiqueta: natural label for the reply ("hoy", "esta semana", "todos").
    """
    mark_past_as_completed()

    conn = _get_db()

    if desde and hasta:
        rows = conn.execute(
            """SELECT * FROM recordatorios
               WHERE activo = 1
               AND (
                   (tipo = 'puntual'  AND fecha BETWEEN ? AND ?)
                OR  tipo = 'diario'
                OR  tipo = 'semanal'
                OR  tipo = 'mensual'
               )
               ORDER BY
                   CASE tipo
                       WHEN 'puntual'  THEN 0
                       WHEN 'diario'   THEN 1
                       WHEN 'semanal'  THEN 1
                       WHEN 'mensual'  THEN 2
                       ELSE 3
                   END,
                   COALESCE(fecha, '') ASC,
                   hora ASC""",
            (desde, hasta),
        ).fetchall()
        # Filter weekly/monthly that fall in the range
        rows = [
            r for r in rows
            if (r["tipo"] not in ("semanal", "mensual"))
            or (r["tipo"] == "semanal"  and _weekly_in_range(r["dias_semana"] or "[0,0,0,0,0,0,0]", desde, hasta))
            or (r["tipo"] == "mensual"  and _monthly_in_range(r["dia_mes"] or 0, desde, hasta))
        ]
    else:
        rows = conn.execute(
            """SELECT * FROM recordatorios
               WHERE activo = 1 AND completado = 0
               ORDER BY
                   CASE tipo
                       WHEN 'puntual'  THEN 0
                       WHEN 'diario'   THEN 1
                       WHEN 'semanal'  THEN 1
                       WHEN 'mensual'  THEN 2
                       ELSE 3
                   END,
                   COALESCE(fecha, '9999-99-99') ASC,
                   COALESCE(dia_mes, 99) ASC,
                   hora ASC"""
        ).fetchall()
    conn.close()

    is_past = etiqueta in ("ayer", "la semana pasada", "el mes pasado")

    if not rows:
        if etiqueta and etiqueta != "todos":
            verb = "tenías" if is_past else "tienes"
            return f"No {verb} ningún recordatorio para {etiqueta}."
        return "No tienes recordatorios pendientes."

    lines = []
    for r in rows:
        hora_natural = _natural_time(r["hora"])
        if r["tipo"] == "diario":
            line = f"- Cada día a {hora_natural}: {r['texto']}"
        elif r["tipo"] == "semanal":
            try:
                days_arr = json.loads(r["dias_semana"] or "[0,0,0,0,0,0,0]")
                active_days = [WEEKDAYS[i] for i, v in enumerate(days_arr) if v]
                if len(active_days) == 7:
                    line = f"- Cada día a {hora_natural}: {r['texto']}"
                else:
                    days_str = ", ".join(active_days) if active_days else "ningún día"
                    line = f"- Cada {days_str} a {hora_natural}: {r['texto']}"
            except Exception:
                line = f"- Semanal a {hora_natural}: {r['texto']}"
        elif r["tipo"] == "mensual":
            line = f"- El día {r['dia_mes']} de cada mes a {hora_natural}: {r['texto']}"
        else:
            fecha_natural = _natural_date(r["fecha"])
            line = f"- El {fecha_natural} a {hora_natural}: {r['texto']}"
        lines.append(line)

    n = len(rows)
    if etiqueta and etiqueta != "todos":
        verb = "tenías" if is_past else "tienes"
        head = f"Para {etiqueta} {verb} {n} recordatorio{'s' if n != 1 else ''}:"
    else:
        head = "Tus recordatorios pendientes:"

    return head + "\n" + "\n".join(lines)


def complete_reminder(recordatorio_id: int) -> str:
    conn = _get_db()
    conn.execute(
        "UPDATE recordatorios SET completado = 1 WHERE id = ?",
        (recordatorio_id,)
    )
    conn.commit()
    conn.close()
    return "Recordatorio completado."


def get_pending_reminders() -> list:
    now       = datetime.now()
    now_time  = now.strftime("%H:%M")
    now_day   = now.day
    now_date  = now.strftime("%Y-%m-%d")

    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM recordatorios WHERE activo = 1 AND completado = 0"
    ).fetchall()
    conn.close()

    now_weekday = now.weekday()  # 0 = monday ... 6 = sunday

    pending = []
    for r in rows:
        if r["hora"] != now_time:
            continue
        if r["tipo"] in ("diario",):
            pending.append(dict(r))
        elif r["tipo"] == "semanal":
            try:
                days = json.loads(r["dias_semana"] or "[0,0,0,0,0,0,0]")
                if days[now_weekday]:
                    pending.append(dict(r))
            except Exception:
                pass
        elif r["tipo"] == "mensual" and r["dia_mes"] == now_day:
            pending.append(dict(r))
        elif r["tipo"] == "puntual" and r["fecha"] == now_date:
            pending.append(dict(r))

    return pending
