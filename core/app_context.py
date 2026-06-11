"""Shared runtime state for Belle's main loop and background workers.

The AppContext is built once in main and passed to every worker and handler, so
they can reach the voice stack, the router and each other without relying on
module-level globals.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voice.audio_manager import AudioManager
    from voice.wake_word import WakeWord
    from voice.stt import STT
    from voice.tts import TTS
    from core.router import Router
    from core.conversation_mode import ConversationMode


class PendingQueue:
    """Thread-safe handoff of items from a worker thread to the main loop.

    A background worker pushes work here and the main loop pops it on its next
    iteration. Every access is guarded by an internal lock.
    """

    def __init__(self) -> None:
        self._items: list[Any] = []
        self._lock = threading.Lock()

    def push(self, item: Any) -> None:
        """Append an item to the end of the queue."""
        with self._lock:
            self._items.append(item)

    def replace(self, item: Any) -> None:
        # Replace everything with a single item, newest wins
        with self._lock:
            self._items.clear()
            self._items.append(item)

    def pop(self) -> Any | None:
        """Remove and return the oldest item, or None if the queue is empty."""
        with self._lock:
            return self._items.pop(0) if self._items else None

    def has_items(self) -> bool:
        """Return True if the queue currently holds at least one item."""
        with self._lock:
            return bool(self._items)


@dataclass
class AppContext:
    """Everything the main loop and the background workers share."""

    # Voice and conversation
    audio: "AudioManager"
    wake: "WakeWord"
    stt: "STT"
    tts: "TTS"
    router: "Router"
    conv: "ConversationMode"

    # Events to stop workers or interrupt the wake word
    stop_event: threading.Event
    tap_event: threading.Event
    chat_event: threading.Event
    med_event: threading.Event
    dc_event: threading.Event
    pres_event: threading.Event

    # Work queues from the workers to the main loop
    medication: PendingQueue
    daily_check: PendingQueue
    chat: PendingQueue
    presence: PendingQueue
