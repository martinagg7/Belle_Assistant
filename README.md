# Belle Assistant

Voice-first assistant for elderly people, built on a Raspberry Pi 5. Belle listens for a wake word, understands natural language through Groq (Whisper + LLaMA), and speaks back via ElevenLabs TTS. A camera module detects falls with YOLO, and a companion Telegram bot keeps family members connected.

Developed as a **Bachelor's Thesis (TFG)** at the University.

## Features

| Category | What it does |
|---|---|
| **Voice loop** | Wake-word detection (Edge Impulse) &rarr; STT (Groq Whisper) &rarr; LLM routing (LLaMA via Groq) &rarr; TTS (ElevenLabs) |
| **Health** | Medication reminders, daily wellness check, RAG over medical reports |
| **Safety** | Fall detection via camera (YOLOv8) with automatic family alerts |
| **Entertainment** | Radio streaming, trivia games, math games, daily news |
| **Communication** | Telegram bot for family messages, voice notes, and photo sharing |
| **Smart home** | Weather reports, reminders/alarms, user profile management |
| **Cloud sync** | Firestore for real-time config and data sync with the family web app |
| **Kiosk UI** | Local web dashboard served via FastAPI, displayed fullscreen on Chromium |

## Architecture

```
┌──────────────────────────────────────────┐
│  Raspberry Pi 5                          │
│                                          │
│  main.py  ──▶  voice loop (wake → STT   │
│                 → Router/LLM → TTS)      │
│                                          │
│  services/server.py   (FastAPI :8000)    │
│  services/telegram_bot.py                │
│  camera/camera_server.py                 │
│  camera/vigilancia.py  (fall detection)  │
│                                          │
│  hardware/  (LEDs, servos)               │
│  rag/       (health RAG pipeline)        │
└──────────┬───────────────────────────────┘
           │
     Firestore / Cloud APIs
           │
┌──────────▼───────────┐
│  Family web app       │
│  (app/index.html)     │
└───────────────────────┘
```

## Requirements

- **Hardware**: Raspberry Pi 5, USB microphone, speaker, camera module, servo kit, LED ring
- **Python**: 3.11+
- **OS**: Raspberry Pi OS (64-bit) with Wayland

## Setup

1. **Clone the repository**

   ```bash
   git clone https://github.com/martinagg7/Belle_Assitant.git
   cd Belle_Assitant
   ```

2. **Create virtual environments**

   ```bash
   python3.11 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt

   # Separate venv for the camera stack (YOLO + OpenCV)
   python3.11 -m venv venv_camera
   venv_camera/bin/pip install -r requirements-camera.txt
   ```

3. **Configure environment variables**

   ```bash
   cp .env.example .env
   # Fill in your API keys (Groq, ElevenLabs, Gemini, Telegram, WeatherAPI)
   ```

4. **Add your Firebase service account**

   Place your `belle-service-account.json` in the project root (this file is git-ignored).

5. **Build the RAG index** (optional, for health reports)

   ```bash
   python -m rag.build_index
   ```

6. **Run**

   ```bash
   ./start.sh
   ```

   This starts all services (API server, Telegram bot, camera, fall detection, kiosk UI) and runs the voice loop in the foreground. Press `Ctrl+C` to stop everything.

## Project structure

```
Belle_Assistant/
├── main.py                 # Entry point — voice loop
├── config.py               # Hardware IDs, model paths, API keys
├── firebase_client.py      # Firestore client singleton
├── core/                   # Voice pipeline, routing, event handlers
├── voice/                  # AudioManager, WakeWord, STT, TTS
├── tools/                  # LLM-callable tools (weather, meds, games…)
├── hardware/               # LED ring and servo control
├── camera/                 # Camera server + YOLO fall detection
├── rag/                    # RAG pipeline for medical reports
├── services/               # FastAPI server + Telegram bot
├── app/                    # Family web dashboard (HTML/JS)
├── prompts/                # System prompts for the LLM
├── start.sh / stop.sh      # Service orchestration scripts
├── requirements.txt        # Main Python dependencies
└── requirements-camera.txt # Camera venv dependencies
```

## API keys

Belle uses the following external services (configured via `.env`):

| Service | Purpose |
|---|---|
| [Groq](https://console.groq.com) | Speech-to-text (Whisper) and LLM (LLaMA) |
| [ElevenLabs](https://elevenlabs.io) | Text-to-speech |
| [Google Gemini](https://ai.google.dev) | Vision (photo descriptions, fall verification) |
| [Telegram Bot API](https://core.telegram.org/bots) | Family communication |
| [WeatherAPI](https://www.weatherapi.com) | Weather forecasts |
| [Firebase/Firestore](https://firebase.google.com) | Cloud sync and data persistence |

## License

This project is licensed under the [MIT License](LICENSE).

## Author

**Martina García González** — Bachelor's Thesis (TFG), 2024–2026
