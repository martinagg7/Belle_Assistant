"""High-level API for the Raspbot V2 camera pan/tilt servos.

Runs in Belle's main venv. It does not drive the hardware directly: it launches
the bridge hardware/bridge/servos_control.py once with the system Python
(/usr/bin/python3), where Raspbot_Lib lives, and sends angles to it over stdin.
The bridge stays open, so moving the servos is instant.

    from hardware import servos
    servos.look_at(120, 70)   # pan=120, tilt=70 degrees
    servos.center()
    servos.close()
"""
from __future__ import annotations

import os
import logging
import subprocess
import threading

logger = logging.getLogger(__name__)

# System Python, Raspbot_Lib lives there
_SYSTEM_PYTHON = "/usr/bin/python3"

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(_BASE_DIR, "bridge", "servos_control.py")

_process: subprocess.Popen | None = None
_lock = threading.Lock()


def _ensure_process() -> subprocess.Popen:
    """Start the bridge if it is not running and return it."""
    global _process
    if _process is None or _process.poll() is not None:
        _process = subprocess.Popen(
            [_SYSTEM_PYTHON, _SCRIPT],
            stdin=subprocess.PIPE,
            text=True,
        )
        print("[servos] bridge started")
    return _process


def look_at(pan: int, tilt: int) -> None:
    """Move the camera to the given pan/tilt angles in degrees."""
    with _lock:
        process = _ensure_process()
        try:
            process.stdin.write(f"{int(pan)} {int(tilt)}\n")
            process.stdin.flush()
        except Exception:
            logger.error("servos: failed to send angles", exc_info=True)


def center() -> None:
    """Point the camera straight ahead (pan=90, tilt=60)."""
    look_at(90, 60)


def close() -> None:
    """Close the servo bridge process."""
    global _process
    with _lock:
        if _process is not None:
            try:
                _process.stdin.close()
                _process.terminate()
                _process.wait(timeout=2)
            except Exception:
                pass
            _process = None
            print("[servos] bridge closed")
