"""Scheduler.

Checks every minute for pending reminders, medication and daily checks and
fires them. Runs inside the server.py process as a background task.
"""
from __future__ import annotations

import logging
from datetime import datetime

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import Config
from tools.recordatorios import (
    get_pending_reminders,
    complete_reminder,
    mark_past_as_completed,
)
from tools.medicacion import get_medications_at, has_log_today
from tools.daily_check import get_checks_at, has_response_today

logger = logging.getLogger(__name__)

SERVER_URL = Config.INTERNAL_SERVER_URL


def start():
    """Start the scheduler and register the cron jobs."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_reminders, trigger="cron", minute="*", id="reminders")
    scheduler.add_job(check_daily_checks, trigger="cron", minute="*", id="daily_checks")
    scheduler.add_job(check_medication, trigger="cron", minute="*", id="medication")

    scheduler.start()
    print("[Scheduler] started — reminders + medication + daily checks")
    return scheduler


async def check_medication():
    """Fire reminders for medication whose time matches the current minute."""
    current_time = datetime.now().strftime("%H:%M")
    meds = get_medications_at(current_time)
    if not meds:
        return

    pending_meds = [m for m in meds if not has_log_today(current_time, m["nombre"])]
    if not pending_meds:
        return

    logger.info("Scheduler: medication %s: %s", current_time, [m["nombre"] for m in pending_meds])

    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            await client.post(
                f"{SERVER_URL}/interno/medicacion",
                json={"horario": current_time, "medicamentos": pending_meds},
            )
        except Exception:
            logger.error("Scheduler: medication post failed", exc_info=True)


async def check_daily_checks():
    """Detect wellbeing checks scheduled for the current minute."""
    current_time = datetime.now().strftime("%H:%M")
    checks = get_checks_at(current_time)

    async with httpx.AsyncClient(timeout=5.0) as client:
        for check in checks:
            if has_response_today(check["id"]):
                continue
            logger.info("Scheduler: daily check: %s", check.get("name", ""))
            try:
                await client.post(f"{SERVER_URL}/interno/daily_check", json=check)
            except Exception:
                logger.error("Scheduler: daily check post failed", exc_info=True)


async def check_reminders():
    """Mark past reminders as done and fire the pending ones."""
    mark_past_as_completed()
    pending = get_pending_reminders()

    async with httpx.AsyncClient(timeout=5.0) as client:
        for r in pending:
            logger.info("Scheduler: firing reminder: %s", r["texto"])

            # 1 Send the event to the screen
            try:
                await client.post(
                    f"{SERVER_URL}/interno/evento",
                    json={
                        "event": "show_recordatorio",
                        "data": {
                            "texto": r["texto"],
                            "hora":  r["hora"],
                        },
                    },
                )
            except Exception:
                logger.error("Scheduler: screen event failed", exc_info=True)

            # 2 Queue text for Belle to say
            try:
                await client.post(
                    f"{SERVER_URL}/interno/tts",
                    json={"texto": f"Recuerda que tienes pendiente: {r['texto']}."},
                )
            except Exception:
                logger.error("Scheduler: tts queue failed", exc_info=True)

            # 3 Mark one-off reminders as completed
            if r["tipo"] == "puntual":
                complete_reminder(r["id"])
