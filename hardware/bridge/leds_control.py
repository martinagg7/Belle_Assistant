#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
"""Bridge to the Raspbot V2 RGB LED ring (WS2812).

Always run with the system Python (/usr/bin/python3), never Belle's venv: it
imports Raspbot_Lib, which lives in the system Python and talks to the board
over I2C (address 0x2B).

Usage:
    /usr/bin/python3 leds_control.py <state> <color>
        state: 0 = off            1 = on
        color: 0 red, 1 green, 2 blue, 3 yellow, 4 purple, 5 cyan, 6 white

Examples:
    /usr/bin/python3 leds_control.py 1 2    # all LEDs blue
    /usr/bin/python3 leds_control.py 1 0    # red (emergency)
    /usr/bin/python3 leds_control.py 0 0    # off
"""
import sys
from Raspbot_Lib import Raspbot


def main():
    if len(sys.argv) < 3:
        print("usage: leds_control.py <state 0|1> <color 0-6>")
        sys.exit(1)

    try:
        state = int(sys.argv[1])
        color = int(sys.argv[2])
    except ValueError:
        print("error: state and color must be integers")
        sys.exit(1)

    bot = Raspbot()
    bot.Ctrl_WQ2812_ALL(state, color)


if __name__ == "__main__":
    main()
