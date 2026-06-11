"""High-level API for the Raspbot V2 RGB LED ring (WS2812).

Runs in Belle's main venv. It does not drive the hardware directly: it runs the
bridge hardware/bridge/leds_control.py with the system Python (/usr/bin/python3),
where Raspbot_Lib lives.

Belle state -> ring color:
    listening()  white (6)  Belle is listening to the user
    speaking()   blue  (2)  Belle is talking (TTS)
    emergency()  red   (0)  an emergency was triggered
    standby()    blue  (2)  idle
    off()        off        on shutdown

Color codes (Raspbot_Lib.Ctrl_WQ2812_ALL):
    0 red, 1 green, 2 blue, 3 yellow, 4 purple, 5 cyan, 6 white
"""
from __future__ import annotations

import os
import logging
import subprocess
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# System Python, Raspbot_Lib lives there
_SYSTEM_PYTHON = "/usr/bin/python3"

# Bridge script path
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(_BASE_DIR, "bridge", "leds_control.py")


def _set(state: int, color: int) -> None:
    # Run the bridge to change the LED ring
    logger.debug("LEDs: state=%s color=%s", state, color)
    try:
        result = subprocess.run(
            [_SYSTEM_PYTHON, _SCRIPT, str(state), str(color)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            logger.error("LEDs: bridge error: %s", result.stderr)
    except Exception:
        logger.error("LEDs: failed to run bridge", exc_info=True)


def listening() -> None:
    """White ring: Belle is listening."""
    with _lock:
        _set(1, 6)


def speaking() -> None:
    """Blue ring: Belle is talking."""
    with _lock:
        _set(1, 2)


def emergency() -> None:
    """Red ring: an emergency was triggered."""
    with _lock:
        _set(1, 0)


def standby() -> None:
    """Blue ring: idle."""
    with _lock:
        _set(1, 2)


def off() -> None:
    """Turn the ring off."""
    with _lock:
        _set(0, 0)
