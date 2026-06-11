#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
"""Persistent bridge for the Raspbot V2 camera pan/tilt servos.

Runs with the system Python (/usr/bin/python3), where Raspbot_Lib (I2C 0x2B) is
installed. hardware/servos.py launches it once and keeps it open, reading angles
from stdin, so tracking can move the servos many times per second without
spawning a process per movement.

Input format (one line per movement):
    "<pan> <tilt>\n"      angles in degrees

Servos:
    S1 (id 1) = pan   0-180  (left/right rotation)
    S2 (id 2) = tilt  0-110  (up/down tilt, clamped for safety)
"""
import sys
from Raspbot_Lib import Raspbot

PAN, TILT = 1, 2
TILT_MAX = 110

bot = Raspbot()

# Initial position
bot.Ctrl_Servo(PAN, 60)
bot.Ctrl_Servo(TILT, 30)

while True:
    line = sys.stdin.readline()
    if not line:        # stdin closed -> the parent finished
        break
    line = line.strip()
    if not line:
        continue
    try:
        parts = line.split()
        pan = max(0, min(180, int(float(parts[0]))))
        tilt = max(0, min(TILT_MAX, int(float(parts[1]))))
        bot.Ctrl_Servo(PAN, pan)
        bot.Ctrl_Servo(TILT, tilt)
    except Exception as e:
        print(f"[servos_control] error with '{line}': {e}", file=sys.stderr)
