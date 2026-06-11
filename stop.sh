#!/bin/bash
# Stop all Belle processes

echo "[Belle] stopping all processes..."
pkill -f "uvicorn"       2>/dev/null
pkill -f "telegram_bot"  2>/dev/null
pkill -f "main.py"       2>/dev/null
pkill -f "camera_server" 2>/dev/null
pkill -f "vigilancia"    2>/dev/null
pkill -f "chromium"      2>/dev/null
echo "[Belle] done."
