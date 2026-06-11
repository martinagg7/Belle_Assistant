"""Tool executor.

Receives the tool name and the JSON arguments Groq decided to use, validates the
arguments with Pydantic and runs the matching Python function.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from config import Config
from tools.time_tool     import get_time
from tools.weather       import get_weather
from tools.news          import get_news
from tools.radio         import play_radio, play_youtube, stop_audio
from tools.profile       import get_profile, update_profile
from tools.familia       import get_family, show_family, get_relative_detail
from tools.recordatorios import create_reminder, list_reminders
from tools.emergencia    import trigger_emergency
from tools.medicacion    import get_pending_medication


# Argument schemas per tool
class TimeArgs(BaseModel):
    pass

class WeatherArgs(BaseModel):
    ciudad: str = Config.CITY
    dia:    str = "hoy"

class NewsArgs(BaseModel):
    categoria: str = "general"

class RadioArgs(BaseModel):
    tipo: str = "noticias"

class YoutubeArgs(BaseModel):
    query: str = ""

class StopAudioArgs(BaseModel):
    pass

class GetProfileArgs(BaseModel):
    pass

class UpdateProfileArgs(BaseModel):
    texto: str = ""

class ShowFamiliaArgs(BaseModel):
    pass

class GetFamiliaArgs(BaseModel):
    pass

class GetFamiliarDetalleArgs(BaseModel):
    nombre: str = ""

class CreateRecordatorioArgs(BaseModel):
    texto:       str         = ""
    hora:        str         = "09:00"
    tipo:        str         = "puntual"
    fecha:       str         = ""
    dia_mes:     int         = 0
    dias_semana: list[int] | None = None

class ListRecordatoriosArgs(BaseModel):
    desde:    str = ""
    hasta:    str = ""
    etiqueta: str = "todos"

class EmergenciaArgs(BaseModel):
    texto_usuario: str = ""
    resumen:       str = ""

class GetMedicacionArgs(BaseModel):
    pass


# Tool registry
TOOLS = {
    "get_time":             (get_time,                 TimeArgs),
    "get_weather":          (get_weather,              WeatherArgs),
    "get_news":             (get_news,                 NewsArgs),
    "play_radio":           (play_radio,               RadioArgs),
    "play_youtube":         (play_youtube,             YoutubeArgs),
    "stop_audio":           (stop_audio,               StopAudioArgs),
    "get_profile":          (get_profile,              GetProfileArgs),
    "update_profile":       (update_profile,           UpdateProfileArgs),
    "get_familia":          (get_family,               GetFamiliaArgs),
    "show_familia":         (show_family,              ShowFamiliaArgs),
    "get_familiar_detalle": (get_relative_detail,      GetFamiliarDetalleArgs),
    "create_recordatorio":  (create_reminder,          CreateRecordatorioArgs),
    "list_recordatorios":   (list_reminders,           ListRecordatoriosArgs),
    "emergencia":           (trigger_emergency,        EmergenciaArgs),
    "get_medicacion":       (get_pending_medication,   GetMedicacionArgs),
}


def execute(name: str, arguments_json: str) -> str:
    """Validate the arguments and run the requested tool, returning its result."""
    if name not in TOOLS:
        return f"error: tool '{name}' not found"

    func, model = TOOLS[name]

    try:
        args_dict = json.loads(arguments_json) if arguments_json and arguments_json != "null" else {}
        args = model(**args_dict)
    except (json.JSONDecodeError, ValidationError) as e:
        return f"error: invalid arguments for {name} — {e}"

    try:
        return func(**args.model_dump())
    except Exception as e:
        return f"error: failed running {name} — {e}"
