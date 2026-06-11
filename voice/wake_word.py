"""Wake word detection for "Belle" using an Edge Impulse model (.eim)."""
from __future__ import annotations

import os
import time
import logging

import numpy as np
from edge_impulse_linux.audio import AudioImpulseRunner

from voice.audio_manager import AudioManager
from config import Config

logger = logging.getLogger(__name__)


class WakeWord:

    def __init__(self, audio_manager: AudioManager) -> None:
        if not os.path.exists(Config.WAKE_WORD_MODEL):
            raise FileNotFoundError(f"Wake word model not found: {Config.WAKE_WORD_MODEL}")
        self._audio = audio_manager
        self._runner: AudioImpulseRunner | None = None
        self._buffer = np.zeros(Config.SAMPLE_RATE_AI, dtype=np.float32)

    def load_model(self) -> None:
        self._runner = AudioImpulseRunner(Config.WAKE_WORD_MODEL)
        self._runner.init()
        print(f"[WakeWord] model loaded: {Config.WAKE_WORD_MODEL}")

    def listen(self, interrupt_event=None) -> bool:
        """Block until "Belle" is detected above WAKE_WORD_THRESHOLD.

        interrupt_event may be a threading.Event or a list of Events.
        """
        print("[WakeWord] watching...")

        events = interrupt_event if isinstance(interrupt_event, list) else ([interrupt_event] if interrupt_event else [])

        try:
            while True:
                if any(e and e.is_set() for e in events):
                    print("[WakeWord] activated by external event")
                    return True

                self._drop_if_lagging()

                if not self._audio.queue.empty():
                    chunk = self._audio.queue.get()
                    chunk_16k = self._audio.resample(chunk)

                    self._buffer = np.roll(self._buffer, -len(chunk_16k))
                    self._buffer[-len(chunk_16k):] = chunk_16k

                    result = self._runner.classify(self._buffer.tolist())

                    if "result" in result:
                        score = result["result"]["classification"].get("belle", 0)
                        if score > 0.3:
                            print(f"\r[WakeWord] belle: {score:.2f}", end="", flush=True)

                        if score > Config.WAKE_WORD_THRESHOLD:
                            print(f"\n[WakeWord] activated (confidence: {score:.2f})")
                            self._buffer = np.zeros(Config.SAMPLE_RATE_AI, dtype=np.float32)
                            return True

                time.sleep(0.01)
        except Exception:
            logger.error("WakeWord: error", exc_info=True)
            return False

    def stop(self) -> None:
        if self._runner:
            self._runner.stop()

    def _drop_if_lagging(self, max_chunks: int = 5) -> None:
        # Keep 1 chunk to retain recent audio context
        if self._audio.queue.qsize() > max_chunks:
            while self._audio.queue.qsize() > 1:
                try:
                    self._audio.queue.get_nowait()
                except Exception:
                    break
