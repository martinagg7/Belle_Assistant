"""Elder profile stored in belle.db (SQLite) and synced with Firestore.

Write rules:
  - The web writes structured fields (name, surname, medical data) -> Firestore
  - Belle appends free-text fields (interests, pets, memories) -> SQLite + Firestore
  - The Pi never overwrites what the web wrote; it only appends with "; "
"""

import os
import json
import sqlite3
import logging

from groq import Groq
from config import Config

logger = logging.getLogger(__name__)

DB_PATH          = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "belle.db")
PROMPTS_DIR      = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
EXTRACTOR_PROMPT = os.path.join(PROMPTS_DIR, "profile_extractor_prompt.md")

# Columns Belle can enrich through conversation
FREE_FIELDS = [
    "gustos_musicales",
    "temas_interes",
    "mascotas",
    "comida_favorita",
    "infancia_historias",
    "familia_descripcion",
    "notas",
]


# SQLite

def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def _load_extractor_prompt() -> str:
    with open(EXTRACTOR_PROMPT, "r", encoding="utf-8") as f:
        return f.read()


def get_elder_name() -> str:
    """Return the elder's first name from SQLite, or empty if there is no profile."""
    try:
        conn = _connect()
        name = conn.execute("SELECT nombre FROM perfil LIMIT 1").fetchone()
        conn.close()
        return name[0] if name and name[0] else ""
    except Exception:
        return ""


def profile_exists() -> bool:
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM perfil")
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False


def get_profile() -> str:
    """Read the full profile from SQLite and return it as text for Groq."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM perfil LIMIT 1")
        row     = cursor.fetchone()
        columns = [desc[0] for desc in cursor.description]
        conn.close()

        if not row:
            return "perfil=vacío"

        data = dict(zip(columns, row))
        parts = []

        if data.get("nombre"):
            full_name = data["nombre"]
            if data.get("apellidos"):
                full_name += f" {data['apellidos']}"
            parts.append(f"Nombre: {full_name}")
        if data.get("ciudad"):           parts.append(f"Ciudad: {data['ciudad']}")
        if data.get("fecha_nacimiento"): parts.append(f"Fecha de nacimiento: {data['fecha_nacimiento']}")
        if data.get("peso_kg"):          parts.append(f"Peso: {data['peso_kg']} kg")
        if data.get("alergias"):         parts.append(f"Alergias: {data['alergias']}")
        if data.get("enfermedades"):     parts.append(f"Enfermedades: {data['enfermedades']}")
        if data.get("mascotas"):         parts.append(f"Mascotas: {data['mascotas']}")
        if data.get("gustos_musicales"): parts.append(f"Gustos musicales: {data['gustos_musicales']}")
        if data.get("comida_favorita"):  parts.append(f"Comida favorita: {data['comida_favorita']}")
        if data.get("infancia_historias"): parts.append(f"Recuerdos de infancia: {data['infancia_historias']}")
        if data.get("temas_interes"):    parts.append(f"Aficiones e intereses: {data['temas_interes']}")
        if data.get("notas"):            parts.append(f"Notas: {data['notas']}")
        if data.get("notas_iniciales"):  parts.append(f"Lo que contó el primer día: {data['notas_iniciales']}")

        # Real relatives from the familia table
        try:
            from datetime import date as _date
            conn2 = _connect()
            family_rows = conn2.execute(
                "SELECT nombre, relacion, ciudad, fecha_nacimiento, trabajo, hobbies, recuerdo, notas "
                "FROM familia WHERE activo = 1 ORDER BY nombre"
            ).fetchall()
            conn2.close()
            if family_rows:
                family_lines = []
                for f in family_rows:
                    try:
                        born = _date.fromisoformat(f[3]) if f[3] else None
                        today = _date.today()
                        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day)) if born else None
                    except Exception:
                        age = None
                    line = f"  - {f[0]} ({f[1]})"
                    if age: line += f", {age} años"
                    if f[2]: line += f", vive en {f[2]}"
                    if f[4]: line += f", {f[4]}"
                    if f[5]: line += f", le gusta {f[5]}"
                    if f[6]: line += f". Recuerdo: {f[6]}"
                    if f[7]: line += f". {f[7]}"
                    family_lines.append(line)
                parts.append("Familiares registrados (usa SOLO estos datos para responder sobre la familia):\n" + "\n".join(family_lines))
        except Exception:
            if data.get("familia_descripcion"): parts.append(f"Familia: {data['familia_descripcion']}")
            elif data.get("familia"):           parts.append(f"Familia: {data['familia']}")

        return "\n".join(parts) if parts else "Todavía no hay información guardada sobre esta persona."

    except Exception as e:
        return f"error: could not read the profile — {e}"


# Firestore sync

def _push_to_firestore(data: dict) -> None:
    """Append the new free-text fields to Firestore (Pi -> Cloud)."""
    try:
        from firebase_client import get_db
        from firebase_admin import firestore as fs

        db      = get_db()
        doc_ref = db.collection("elder_profile").document("main")
        snap    = doc_ref.get()
        current = snap.to_dict() if snap.exists else {}

        update_data = {}
        for field, value in data.items():
            if not value or field not in FREE_FIELDS:
                continue
            existing = current.get(field, "") or ""
            if isinstance(existing, list):
                # This field is an array in Firestore
                if value not in existing:
                    update_data[field] = existing + [value]
            else:
                if existing and value not in existing:
                    update_data[field] = f"{existing}; {value}"
                elif not existing:
                    update_data[field] = value

        if update_data:
            update_data["updated_at"] = fs.SERVER_TIMESTAMP
            doc_ref.set(update_data, merge=True)
            logger.info("Firestore: profile updated: %s", list(update_data.keys()))

    except Exception:
        logger.error("Firestore: error updating profile", exc_info=True)


def sync_from_firestore() -> None:
    """Download the profile from Firestore into SQLite (Cloud -> Pi)."""
    try:
        from firebase_client import get_db

        db   = get_db()
        snap = db.collection("elder_profile").document("main").get()

        if not snap.exists:
            logger.info("Firestore: no cloud profile yet")
            return

        data = snap.to_dict()

        conn   = _connect()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM perfil")
        exists = cursor.fetchone()[0] > 0

        allowed_fields = [
            "nombre", "apellidos", "ciudad", "fecha_nacimiento", "photo_url",
            "peso_kg", "alergias", "enfermedades",
        ] + FREE_FIELDS

        sets   = []
        values = []
        for field in allowed_fields:
            if field not in data or data[field] is None:
                continue
            val = data[field]
            if isinstance(val, list):
                val = '; '.join(str(v) for v in val if v)
            sets.append(field)
            values.append(val)

        if not sets:
            logger.info("Firestore: no recognized fields in the profile")
            conn.close()
            return

        if exists:
            set_clause = ', '.join(f"{c} = ?" for c in sets)
            cursor.execute(
                f"UPDATE perfil SET {set_clause}, actualizado_en = CURRENT_TIMESTAMP WHERE id = 1",
                values,
            )
        else:
            placeholders = ', '.join(['?'] * len(sets))
            cursor.execute(
                f"INSERT INTO perfil ({', '.join(sets)}) VALUES ({placeholders})",
                values,
            )

        conn.commit()
        conn.close()
        logger.info("Firestore: profile synced to SQLite")

    except Exception:
        logger.error("Firestore: error syncing to SQLite", exc_info=True)


# Update profile through conversation

def update_profile(texto: str) -> str:
    """Extract what the person just said with Groq, append to SQLite and Firestore."""
    try:
        client = Groq(api_key=Config.GROQ_API_KEY)
        response = client.chat.completions.create(
            model=Config.GROQ_MODEL,
            messages=[
                {"role": "system", "content": _load_extractor_prompt()},
                {"role": "user",   "content": texto}
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )

        data = json.loads(response.choices[0].message.content)

        if not data:
            return "actualizado=sin_datos_nuevos"

        # 1 Update SQLite
        conn   = _connect()
        cursor = conn.cursor()

        for field in FREE_FIELDS:
            if field not in data or not data[field]:
                continue

            cursor.execute(f"SELECT {field} FROM perfil WHERE id = 1")
            row = cursor.fetchone()
            current_value = row[0] if row else None

            if current_value:
                new_value = f"{current_value}; {data[field]}"
            else:
                new_value = data[field]

            cursor.execute(
                f"UPDATE perfil SET {field} = ?, actualizado_en = CURRENT_TIMESTAMP WHERE id = 1",
                (new_value,)
            )

        conn.commit()
        conn.close()

        # 2 Push to Firestore, does not block on failure
        _push_to_firestore(data)

        return "actualizado=perfil_actualizado"

    except Exception as e:
        return f"error: could not update the profile — {e}"
