"""Audio playback for Belle: fixed stations with fallback and YouTube search."""

import subprocess
import threading

# Background mpv process
_mpv_process: subprocess.Popen | None = None

STATIONS = {
    "musica": [
        "https://cadena100-cope.flumotion.com/chunks.m3u8",
        "https://playerservices.streamtheworld.com/api/livestream-redirect/LOS40_CLASSIC.mp3",
    ],
    "noticias": [
        "https://emisoras.cadenaser.com/CADENASER",
        "https://atres-live.ondacero.es/live/ondacero/bitrate_1.m3u8",
    ],
}

CONNECT_TIMEOUT = 5  # switch to the next station on error


def play_radio(tipo: str = "noticias") -> str:
    """Play a station (music or news)."""
    global _mpv_process

    tipo = tipo.lower()
    if tipo not in STATIONS:
        tipo = "musica"

    urls = STATIONS[tipo]
    stop_audio()

    for url in urls:
        try:
            process = subprocess.Popen(
                ["mpv", "--no-video", "--really-quiet", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                process.wait(timeout=CONNECT_TIMEOUT)
                continue
            except subprocess.TimeoutExpired:
                _mpv_process = process
                name = "música" if tipo == "musica" else "noticias de radio"
                return f"reproduciendo={name}, url={url}"
        except Exception:
            continue

    return "error: no se pudo conectar con ninguna emisora"


def play_youtube(query: str) -> str:
    """Search YouTube and play the first result (open query so Groq decides)."""
    global _mpv_process

    stop_audio()

    def _search_and_play():
        global _mpv_process
        try:
            result = subprocess.run(
                ["yt-dlp", "-f", "bestaudio", "--get-url", "--no-playlist", f"ytsearch1:{query}"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return
            url = result.stdout.strip().split("\n")[0]
            _mpv_process = subprocess.Popen(
                ["mpv", "--no-video", "--really-quiet", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    # In a thread because the YouTube search can be slow
    threading.Thread(target=_search_and_play, daemon=True).start()

    return f"buscando={query}, fuente=youtube"


def stop_audio() -> str:
    """Stop audio if something is playing."""
    global _mpv_process

    if _mpv_process is None:
        return "estado=sin_audio"

    try:
        _mpv_process.terminate()
        _mpv_process.wait(timeout=3)
    except Exception:
        try:
            _mpv_process.kill()
        except Exception:
            pass
    finally:
        _mpv_process = None

    return "estado=audio_detenido"


def audio_status() -> str:
    """Return whether something is currently playing."""
    global _mpv_process

    if _mpv_process is None:
        return "estado=sin_audio"

    if _mpv_process.poll() is None:
        return "estado=reproduciendo"
    _mpv_process = None
    return "estado=sin_audio"
