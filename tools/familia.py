"""Belle's family tool: reads relatives from SQLite."""

import json
import sqlite3
from pathlib import Path
from datetime import date

from config import Config

DB_PATH    = Path(__file__).parent.parent / "data" / "belle.db"
SERVER_URL = Config.INTERNAL_SERVER_URL


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _calculate_age(fecha_nacimiento: str) -> int | None:
    if not fecha_nacimiento:
        return None
    try:
        birth = date.fromisoformat(fecha_nacimiento)
        today = date.today()
        return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    except Exception:
        return None


def get_family() -> str:
    """Medium summary of relatives: city, job and one hobby. No full memories."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT nombre, relacion, ciudad, fecha_nacimiento, trabajo, hobbies FROM familia WHERE activo = 1 ORDER BY nombre"
    ).fetchall()
    conn.close()

    if not rows:
        return "No hay familiares registrados todavía."

    lines = []
    for r in rows:
        line = f"- {r['nombre']} ({r['relacion']})"
        age = _calculate_age(r["fecha_nacimiento"])
        if age is not None:
            line += f", {age} años"
        parts = []
        if r["ciudad"]:  parts.append(f"vive en {r['ciudad']}")
        if r["trabajo"]: parts.append(r["trabajo"])
        if r["hobbies"]:
            first_hobby = r["hobbies"].split(",")[0].strip()
            parts.append(f"le gusta {first_hobby}")
        if parts:
            line += ": " + ", ".join(parts)
        lines.append(line)

    return "Familiares:\n" + "\n".join(lines)


def get_relative_detail(nombre: str) -> str:
    """Return full info about one relative for Groq."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT nombre, relacion, ciudad, fecha_nacimiento, trabajo, hobbies, recuerdo, notas "
        "FROM familia WHERE activo = 1 AND (LOWER(nombre) LIKE ? OR LOWER(relacion) LIKE ?)",
        (f"%{nombre.lower()}%", f"%{nombre.lower()}%")
    ).fetchall()
    conn.close()

    if not rows:
        return f"No encuentro a ningún familiar llamado {nombre}."

    r = rows[0]
    age = _calculate_age(r["fecha_nacimiento"])
    parts = [f"{r['nombre']} es tu {r['relacion']}"]
    if age:           parts.append(f"tiene {age} años")
    if r["ciudad"]:   parts.append(f"vive en {r['ciudad']}")
    if r["trabajo"]:  parts.append(r["trabajo"])
    if r["hobbies"]:  parts.append(f"le gusta {r['hobbies']}")
    if r["recuerdo"]: parts.append(f"recuerdo especial: {r['recuerdo']}")
    if r["notas"]:    parts.append(r["notas"])
    return ". ".join(parts) + "."


def show_family() -> str:
    """Return a JSON list of relatives with all their info for the router."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT id, nombre, relacion, ciudad, telefono, trabajo, hobbies, foto_path, "
        "fecha_nacimiento, recuerdo, notas FROM familia WHERE activo = 1 ORDER BY nombre"
    ).fetchall()
    conn.close()

    if not rows:
        return "sin_familiares"

    relatives = []
    for f in rows:
        photo_path = f["foto_path"] or ""
        if photo_path.startswith("http"):
            photo_url = photo_path
        else:
            filename = Path(photo_path).name if photo_path else ""
            photo_url = f"{SERVER_URL}/fotos/{filename}" if filename else ""

        age = _calculate_age(f["fecha_nacimiento"])
        relatives.append({
            "id":       f["id"],
            "nombre":   f["nombre"],
            "relacion": f["relacion"],
            "ciudad":   f["ciudad"]   or "",
            "telefono": f["telefono"] or "",
            "trabajo":  f["trabajo"]  or "",
            "hobbies":  f["hobbies"]  or "",
            "recuerdo": f["recuerdo"] or "",
            "notas":    f["notas"]    or "",
            "edad":     age,
            "foto_url": photo_url,
        })

    return json.dumps(relatives)
