"""Microphone capture and ALSA volume setup for Belle.

Opens the mic input stream, resamples from 48kHz to 16kHz and keeps a
thread-safe queue with the audio chunks.

Main methods:
    start()             start capture
    stop()              close the stream
    resample()          downsample 48kHz -> 16kHz
    clear_queue()       drop buffered audio before listening
    configure_volumes() set ALSA volumes via amixer
"""
from __future__ import annotations

import queue
import logging
import subprocess

import numpy as np
import sounddevice as sd

from config import Config

logger = logging.getLogger(__name__)


class AudioManager:
    """Captures mic audio into a queue and configures the speaker volume."""

    def __init__(self) -> None:
        self.queue: queue.Queue = queue.Queue()
        self._resample_factor = Config.SAMPLE_RATE_MIC // Config.SAMPLE_RATE_AI
        self._stream: sd.InputStream | None = None

    def start(self) -> None:
        """Open the mic stream and start filling the queue."""
        self._stream = sd.InputStream(
            device=Config.MIC_ID,
            channels=1,
            samplerate=Config.SAMPLE_RATE_MIC,
            dtype="int16",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        """Close the mic stream cleanly."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _callback(self, indata, frames, time, status) -> None:
        if status:
            logger.warning("AudioManager: stream status: %s", status)
        self.queue.put(indata.copy())

    def resample(self, chunk: np.ndarray) -> np.ndarray:
        """Downsample a 48kHz chunk to 16kHz by decimation (factor 3)."""
        return chunk.flatten()[:: self._resample_factor]

    def clear_queue(self) -> None:
        """Empty the queue before listening, dropping buffered audio."""
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break

    def _find_alsa_card(self, name: str) -> str | None:
        """Find the ALSA card number whose name matches, using aplay -l."""
        try:
            out = subprocess.run(["aplay", "-l"], capture_output=True, text=True).stdout
            for line in out.splitlines():
                if name.lower() in line.lower() and line.startswith("card"):
                    return line.split(":")[0].replace("card", "").strip()
        except Exception:
            logger.debug("AudioManager: could not list ALSA cards", exc_info=True)
        return None

    def _find_volume_numid(self, card: str) -> str | None:
        """Find the numid of the playback volume control on an ALSA card."""
        try:
            out = subprocess.run(
                ["amixer", "-c", card, "controls"],
                capture_output=True, text=True,
            ).stdout
            for line in out.splitlines():
                if "Playback Volume" in line or "'PCM'" in line:
                    for part in line.split(","):
                        if "numid" in part:
                            return part.split("=")[1].strip()
        except Exception:
            logger.debug("AudioManager: could not list mixer controls", exc_info=True)
        return None

    def configure_volumes(self) -> None:
        """Set the speaker volume via amixer (ALSA).

        Finds the card by name and the volume control by numid dynamically.
        """
        try:
            card = self._find_alsa_card(Config.SPEAKER_NAME)
            args = ["-c", card] if card else []
            numid = self._find_volume_numid(card) if card else None

            if numid:
                r = subprocess.run(
                    ["amixer"] + args + ["cset", f"numid={numid}", Config.VOL_SPEAKER],
                    capture_output=True,
                )
                if r.returncode == 0:
                    logger.info("AudioManager: speaker volume %s (card=%s, numid=%s)",
                                Config.VOL_SPEAKER, card, numid)
                    return

            # Fallback with known controls
            for control in ["PCM", "Master"]:
                r = subprocess.run(
                    ["amixer"] + args + ["sset", control, Config.VOL_SPEAKER],
                    capture_output=True,
                )
                if r.returncode == 0:
                    logger.info("AudioManager: speaker volume %s (%s)", Config.VOL_SPEAKER, control)
                    return

            logger.warning("AudioManager: could not set speaker volume")
        except Exception:
            logger.error("AudioManager: error configuring volumes", exc_info=True)
