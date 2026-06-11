"""create_db.py

Creates (or migrates) Belle's SQLite database with all the required tables.
Run directly to initialize or apply migrations: python create_db.py

Tables:
  1. perfil           — the elder's personal and medical data
  2. familia          — relatives synced from Firestore family_members
  3. medicamentos     — medications synced from Firestore medications
  4. medication_logs  — daily medication-intake log
  5. recordatorios    — reminders synced from Firestore reminders
  6. partidas         — permanent game history
  7. fallos_sesion    — questions missed in the current game (cleared at the end)
  8. telegram_usuarios — relatives registered in the Telegram bot
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "belle.db")


def _columns(cursor, table: str) -> set:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _migrate(cursor) -> None:
    """Add missing columns to existing tables without losing data."""

    # perfil
    cols = _columns(cursor, "perfil")
    for name, col_type in [
        ("apellidos",            "TEXT"),
        ("mascotas",             "TEXT"),
        ("comida_favorita",      "TEXT"),
        ("infancia_historias",   "TEXT"),
        ("familia_descripcion",  "TEXT"),
        ("peso_kg",              "REAL"),
        ("alergias",             "TEXT"),
        ("enfermedades",         "TEXT"),
        ("photo_url",            "TEXT"),
    ]:
        if name not in cols:
            cursor.execute(f"ALTER TABLE perfil ADD COLUMN {name} {col_type}")
            print(f"[DB] migration: perfil.{name}")

    # familia
    cols = _columns(cursor, "familia")
    for name, col_type in [
        ("firestore_id",    "TEXT"),
        ("email",           "TEXT"),
        ("fecha_nacimiento","TEXT"),
        ("trabajo",         "TEXT"),
        ("hobbies",         "TEXT"),
        ("recuerdo",        "TEXT"),
        ("notas",           "TEXT"),
    ]:
        if name not in cols:
            cursor.execute(f"ALTER TABLE familia ADD COLUMN {name} {col_type}")
            print(f"[DB] migration: familia.{name}")

    # recordatorios
    cols = _columns(cursor, "recordatorios")
    for name, col_type in [
        ("firestore_id", "TEXT"),
        ("dias_semana",  "TEXT"),
        ("origin",       "TEXT DEFAULT 'pi'"),
    ]:
        if name not in cols:
            cursor.execute(f"ALTER TABLE recordatorios ADD COLUMN {name} {col_type}")
            print(f"[DB] migration: recordatorios.{name}")

    # partidas
    cols = _columns(cursor, "partidas")
    if "tipo" not in cols:
        cursor.execute("ALTER TABLE partidas ADD COLUMN tipo TEXT DEFAULT 'trivial'")
        print("[DB] migration: partidas.tipo")

    # Medications, may not exist yet
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='medicamentos'")
    if cursor.fetchone():
        cols = _columns(cursor, "medicamentos")
        for name, col_type in [
            ("firestore_id", "TEXT"),
            ("hora",         "TEXT"),
        ]:
            if name not in cols:
                cursor.execute(f"ALTER TABLE medicamentos ADD COLUMN {name} {col_type}")
                print(f"[DB] migration: medicamentos.{name}")

    # Medication logs, may not exist yet
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='medication_logs'")
    if cursor.fetchone():
        cols = _columns(cursor, "medication_logs")
        if "confirmado_por" not in cols:
            cursor.execute("ALTER TABLE medication_logs ADD COLUMN confirmado_por TEXT DEFAULT 'voz'")
            print("[DB] migration: medication_logs.confirmado_por")


def create_database() -> None:
    conn   = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1 Profile
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS perfil (
            id                   INTEGER PRIMARY KEY,
            nombre               TEXT,
            apellidos            TEXT,
            ciudad               TEXT,
            fecha_nacimiento     TEXT,
            photo_url            TEXT,
            peso_kg              REAL,
            alergias             TEXT,
            enfermedades         TEXT,
            mascotas             TEXT,
            gustos_musicales     TEXT,
            comida_favorita      TEXT,
            infancia_historias   TEXT,
            temas_interes        TEXT,
            familia_descripcion  TEXT,
            notas                TEXT,
            edad                 TEXT,
            notas_iniciales      TEXT,
            familia              TEXT,
            creado_en            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            actualizado_en       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 2 Family
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS familia (
            id               INTEGER PRIMARY KEY,
            firestore_id     TEXT    UNIQUE,
            nombre           TEXT    NOT NULL,
            relacion         TEXT    NOT NULL,
            telefono         TEXT,
            email            TEXT,
            ciudad           TEXT,
            fecha_nacimiento TEXT,
            trabajo          TEXT,
            hobbies          TEXT,
            recuerdo         TEXT,
            notas            TEXT,
            foto_path        TEXT,
            activo           INTEGER DEFAULT 1,
            creado_en        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 3 Medications
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS medicamentos (
            id           INTEGER PRIMARY KEY,
            firestore_id TEXT    UNIQUE,
            nombre       TEXT    NOT NULL,
            dosis        TEXT,
            horario      TEXT    NOT NULL,
            hora         TEXT,
            notas        TEXT,
            activo       INTEGER DEFAULT 1,
            creado_en    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 4 Medication logs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS medication_logs (
            id              INTEGER PRIMARY KEY,
            medicamento_id  TEXT,
            nombre          TEXT    NOT NULL,
            fecha           TEXT    NOT NULL,
            horario         TEXT    NOT NULL,
            estado          TEXT    NOT NULL,
            confirmado_por  TEXT    DEFAULT 'voz',
            creado_en       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 5 Reminders
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recordatorios (
            id           INTEGER PRIMARY KEY,
            firestore_id TEXT,
            texto        TEXT    NOT NULL,
            tipo         TEXT    DEFAULT 'puntual',
            fecha        TEXT,
            hora         TEXT    NOT NULL,
            dia_mes      INTEGER,
            dias_semana  TEXT,
            origin       TEXT    DEFAULT 'pi',
            activo       INTEGER DEFAULT 1,
            completado   INTEGER DEFAULT 0,
            creado_en    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 6 Games
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS partidas (
            id                  INTEGER PRIMARY KEY,
            fecha               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            tipo                TEXT    DEFAULT 'trivial',
            nivel_alcanzado     TEXT,
            puntos_final        INTEGER DEFAULT 0,
            preguntas_total     INTEGER DEFAULT 0,
            aciertos            INTEGER DEFAULT 0,
            fallos              INTEGER DEFAULT 0,
            racha_maxima        INTEGER DEFAULT 0,
            duracion_minutos    REAL    DEFAULT 0,
            categorias_falladas TEXT
        )
    """)

    # 7 Game session misses
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fallos_sesion (
            id                 INTEGER PRIMARY KEY,
            pregunta           TEXT,
            respuesta_correcta TEXT,
            categoria          TEXT,
            veces_fallada      INTEGER DEFAULT 1
        )
    """)

    # 8 Telegram users
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS telegram_usuarios (
            id        INTEGER PRIMARY KEY,
            chat_id   INTEGER UNIQUE NOT NULL,
            nombre    TEXT,
            activo    INTEGER DEFAULT 1,
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 9 Daily checks
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_checks (
            id           INTEGER PRIMARY KEY,
            firestore_id TEXT    UNIQUE,
            nombre       TEXT    NOT NULL,
            hora         TEXT    NOT NULL,
            preguntas    TEXT    NOT NULL,
            activo       INTEGER DEFAULT 1,
            creado_en    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migrations for existing databases
    _migrate(cursor)

    conn.commit()
    conn.close()
    print(f"[DB] database ready: {os.path.abspath(DB_PATH)}")


if __name__ == "__main__":
    create_database()
