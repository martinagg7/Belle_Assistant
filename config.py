"""config.py

Configuration:
    - Hardware IDs (microphone and speaker) by name. To change the speaker/mic,
      just update the name here.
    - Local model paths
    - API keys read from .env
"""

import os
import sounddevice as sd
from dotenv import load_dotenv

load_dotenv()


def _find_device(name: str, kind: str = "input") -> int | None:
    """Walk the system audio devices and return the matching ID.

    kind: "input" for microphones, "output" for speakers.
    """
    for i, dev in enumerate(sd.query_devices()):
        matches = name.lower() in dev["name"].lower()
        is_kind = (
            dev["max_input_channels"] > 0 if kind == "input"
            else dev["max_output_channels"] > 0
        )
        if matches and is_kind:
            return i
    return None


class Config:

    # Audio device names
    MIC_NAME     = "C-Media"
    SPEAKER_NAME = "GeneralPlus"

    MIC_ID     = _find_device(MIC_NAME, "input")
    SPEAKER_ID = _find_device(SPEAKER_NAME, "output")

    # ALSA route for subprocesses that use aplay
    SPEAKER_ALSA = "plughw:CARD=Device,DEV=0"

    # Mic at 48kHz, AI at 16kHz, resample factor = 3
    SAMPLE_RATE_MIC = 48000
    SAMPLE_RATE_AI  = 16000

    # Volumes
    MIXER_CONTROL_MIC     = "Mic"
    MIXER_CONTROL_SPEAKER = "PCM"
    VOL_MIC               = "100%"
    VOL_SPEAKER           = "100%"

    # Local models
    WAKE_WORD_MODEL = "models/wake_word.eim"

    # Internal FastAPI server, event bus between modules
    INTERNAL_SERVER_URL = "http://localhost:8000"

    # Groq
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    GROQ_MODEL = "llama-3.3-70b-versatile"
    MAX_HISTORY_TURNS = 4
    STT_TIMEOUT = 10

    # STT, silence detection
    STT_MODEL      = "whisper-large-v3-turbo"
    STT_LANGUAGE   = "es"
    CHUNK_SECS     = 0.4
    RMS_THRESHOLD  = 300
    SILENCE_CUTOFF = 12
    MIN_DURATION   = 0.4

    # Weather
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
    CITY = "Madrid"

    # Wake word
    WAKE_WORD_THRESHOLD = 1.5
    WAKE_WORD_PAUSE = 0.1

    # ElevenLabs
    ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY")
    ELEVENLABS_VOICE_ID = "tXgbXPnsMpKXkuTgvE3h"
    ELEVENLABS_MODEL    = "eleven_flash_v2_5"
    ELEVENLABS_SPEED    = 1.15
    ELEVENLABS_VOLUME   = 80

    # Telegram
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
