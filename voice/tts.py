"""Text-to-speech using ElevenLabs (Elena, eleven_flash_v2_5).

Receives the Groq stream chunk by chunk, accumulates the full text and sends it
to ElevenLabs in streaming for real-time playback.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time

from elevenlabs.client import ElevenLabs
from elevenlabs import VoiceSettings

from config import Config

logger = logging.getLogger(__name__)


class TTS:
    """Synthesizes speech with ElevenLabs.

    speak(groq_stream): accumulate text from the Groq stream and synthesize it.
    speak_text(text):   synthesize a fixed string.
    """

    def __init__(self) -> None:
        self.client = ElevenLabs(api_key=Config.ELEVENLABS_API_KEY)
        self.speaking = threading.Event()

    def _play_stream(self, audio_stream) -> None:
        """Pipe ElevenLabs MP3 chunks to mpv over stdin."""
        process = subprocess.Popen(
            ["mpv", "--no-video", "--really-quiet", f"--volume={Config.ELEVENLABS_VOLUME}", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for chunk in audio_stream:
                if chunk:
                    process.stdin.write(chunk)
                    process.stdin.flush()
            process.stdin.close()
            process.wait()
        except Exception:
            logger.error("TTS: playback error", exc_info=True)
        finally:
            if process.poll() is None:
                process.terminate()

    def _stream_elevenlabs(self, text: str) -> None:
        t0 = time.time()
        audio_stream = self.client.text_to_speech.stream(
            voice_id=Config.ELEVENLABS_VOICE_ID,
            text=text,
            model_id=Config.ELEVENLABS_MODEL,
            voice_settings=VoiceSettings(
                stability=0.5,
                similarity_boost=0.8,
                style=0.0,
                use_speaker_boost=True,
                speed=Config.ELEVENLABS_SPEED,
            ),
            output_format="mp3_44100_128",
        )
        logger.debug("TTS: ElevenLabs stream started in %.2fs", time.time() - t0)
        self._play_stream(audio_stream)

    def speak(self, groq_stream) -> str:
        """Read the Groq stream, echo it to stdout and synthesize the full reply."""
        full_response = ""
        t0 = time.time()

        print("[TTS] ", end="", flush=True)
        for chunk in groq_stream:
            content = chunk.choices[0].delta.content
            if content:
                print(content, end="", flush=True)
                full_response += content
        print()
        logger.debug("TTS: Groq stream complete in %.2fs", time.time() - t0)

        if not full_response.strip():
            return full_response

        self.speaking.set()
        try:
            self._stream_elevenlabs(full_response)
        except Exception:
            logger.error("TTS: ElevenLabs error", exc_info=True)
        finally:
            self.speaking.clear()

        return full_response

    def speak_text(self, text: str) -> None:
        """Synthesize a fixed string with ElevenLabs."""
        if not text.strip():
            return
        self.speaking.set()
        try:
            self._stream_elevenlabs(text)
        except Exception:
            logger.error("TTS: speak_text error", exc_info=True)
        finally:
            self.speaking.clear()
