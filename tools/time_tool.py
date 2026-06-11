"""Returns the current system date and time in natural Spanish."""

from datetime import datetime

DAYS = {
    "Monday": "lunes", "Tuesday": "martes", "Wednesday": "miércoles",
    "Thursday": "jueves", "Friday": "viernes", "Saturday": "sábado",
    "Sunday": "domingo",
}

MONTHS = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}


def get_time() -> str:
    """Return today's date and time as a Spanish sentence for Groq."""
    now = datetime.now()
    weekday = DAYS[now.strftime("%A")]
    day = now.day
    month = MONTHS[now.month]
    year = now.year
    time_str = now.strftime("%H:%M")
    return f"Hoy es {weekday} {day} de {month} de {year}, son las {time_str}."
