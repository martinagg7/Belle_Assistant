"""Entry point for Belle.

Builds the voice stack and router, starts the background workers and runs the
main loop, dispatching each iteration to the handlers in core.event_handlers.
"""
from __future__ import annotations

import logging
import threading

from voice.audio_manager import AudioManager
from voice.wake_word import WakeWord
from voice.stt import STT
from voice.tts import TTS
from core.router import Router
from core.onboarding import launch as launch_onboarding
from core.conversation_mode import ConversationMode
from core.app_context import AppContext, PendingQueue
from core.background_workers import start_workers
from core import event_handlers as handlers
from hardware import leds

logger = logging.getLogger(__name__)


def _center_camera() -> None:
    """Move the camera to its default position (this also powers the servo bridge)."""
    try:
        from hardware import servos
        servos.look_at(60, 30)
        print("[Belle] camera set to (60, 30)")
    except Exception:
        logger.warning("Could not position the camera at startup", exc_info=True)


def _build_context() -> AppContext:
    """Create and wire every component Belle needs into a single context."""
    audio = AudioManager()
    audio.configure_volumes()
    audio.start()

    wake = WakeWord(audio)
    stt = STT(audio)
    tts = TTS()
    router = Router(tts_callback=tts.speak_text, stt_callback=stt.listen)
    conv = ConversationMode()

    wake.load_model()

    return AppContext(
        audio=audio,
        wake=wake,
        stt=stt,
        tts=tts,
        router=router,
        conv=conv,
        stop_event=threading.Event(),
        tap_event=threading.Event(),
        chat_event=threading.Event(),
        med_event=threading.Event(),
        dc_event=threading.Event(),
        pres_event=threading.Event(),
        medication=PendingQueue(),
        daily_check=PendingQueue(),
        chat=PendingQueue(),
        presence=PendingQueue(),
    )


def run() -> None:
    """Start Belle and run the main loop until interrupted."""
    print("[Belle] starting...")
    _center_camera()

    ctx = _build_context()
    launch_onboarding(ctx.tts)
    start_workers(ctx)

    print("[Belle] ready. Say her name to activate.")
    try:
        while True:
            # Pending work, in priority order
            if handlers.handle_medication(ctx):
                continue
            if handlers.handle_daily_check(ctx):
                continue
            if handlers.handle_family_message(ctx):
                continue
            if handlers.handle_presence_check(ctx):
                continue
            handlers.handle_interaction(ctx)
    except KeyboardInterrupt:
        print("\n[Belle] shutting down...")
    finally:
        ctx.stop_event.set()
        leds.off()
        ctx.audio.stop()
        ctx.wake.stop()
        ctx.router.clear_history()
        print("[Belle] stopped.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    run()
