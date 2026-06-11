"""Speech-to-text using Groq Whisper.

Captures mic chunks at 48kHz, detects silence by RMS and sends the full audio to
the Groq API when the person stops talking.
"""
from __future__ import annotations

import io
import time
import wave
import logging
import threading

import numpy as np
from groq import Groq

from voice.audio_manager import AudioManager
from config import Config

logger = logging.getLogger(__name__)


class STT:

    def __init__(self, audio_manager: AudioManager) -> None:
        if not Config.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY not found in .env")
        self._audio = audio_manager
        self._client = Groq(api_key=Config.GROQ_API_KEY)
        self.listening = threading.Event()

    def listen(self) -> str:
        """Block until speech then silence, then transcribe with Groq Whisper.

        Returns the transcribed text, or an empty string if STT_TIMEOUT seconds
        pass without detecting any speech.
        """
        self._audio.clear_queue()
        self.listening.set()

        chunks_48k = []
        silence_chunks = 0
        voice_detected = False
        cut_chunks = int(Config.SILENCE_CUTOFF / Config.CHUNK_SECS)
        start_time = time.time()

        print("[STT] listening...")

        try:
            while True:
                if time.time() - start_time > Config.STT_TIMEOUT:
                    print("\n[STT] timeout — no response")
                    return ""

                self._drop_if_lagging()

                if self._audio.queue.empty():
                    time.sleep(0.01)
                    continue

                chunk = self._audio.queue.get()
                rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))

                if rms > Config.RMS_THRESHOLD:
                    voice_detected = True
                    chunks_48k.append(chunk.copy())
                    silence_chunks = 0
                    start_time = time.time()
                else:
                    if voice_detected:
                        chunks_48k.append(chunk.copy())
                        silence_chunks += 1

                        if silence_chunks >= cut_chunks:
                            audio_16k = self._audio.resample(
                                np.concatenate(chunks_48k).flatten()
                            )
                            duration = len(audio_16k) / Config.SAMPLE_RATE_AI

                            if duration < Config.MIN_DURATION:
                                chunks_48k = []
                                silence_chunks = 0
                                voice_detected = False
                                start_time = time.time()
                                continue

                            text = self._transcribe(audio_16k)
                            if text:
                                print(f"[STT] recognized: {text}")
                            return text
        except Exception:
            logger.error("STT: capture error", exc_info=True)
            return ""
        finally:
            self.listening.clear()

    def _transcribe(self, audio_16k: np.ndarray) -> str:
        """Pack the audio into WAV and send it to Groq Whisper."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(Config.SAMPLE_RATE_AI)
            wf.writeframes(audio_16k.tobytes())
        wav_bytes = buf.getvalue()
        buf.close()

        try:
            r = self._client.audio.transcriptions.create(
                file=("audio.wav", wav_bytes),
                model=Config.STT_MODEL,
                language=Config.STT_LANGUAGE,
                response_format="json",
                temperature=0.0,
            )
            return r.text.strip()
        except Exception:
            logger.error("STT: Groq error", exc_info=True)
            return ""

    def _drop_if_lagging(self, max_chunks: int = 20) -> None:
        if self._audio.queue.qsize() > max_chunks:
            self._audio.clear_queue()
