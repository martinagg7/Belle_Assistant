"""Belle hardware package.

Holds the layer that talks to the Raspbot V2 robot (Yahboom).

Two levels:
  - High-level modules (this package): imported from Belle's venv, they expose a
    clean API (for example leds.speaking()).
  - bridge/: scripts run with the system Python (/usr/bin/python3) because they
    import Raspbot_Lib (I2C 0x2B). They are not imported from the venv; they are
    launched with subprocess.
"""
