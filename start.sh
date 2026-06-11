#!/bin/bash
# Start the whole Belle stack and stop it cleanly on exit

# Move to the project directory
PROJECT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$PROJECT_DIR" || exit 1

LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

# Main virtual environment
# shellcheck disable=SC1091
source venv/bin/activate

# Stop all background processes on exit
stop_all() {
    echo "[Belle] stopping background processes..."
    pkill -f "uvicorn"       2>/dev/null
    pkill -f "telegram_bot"  2>/dev/null
    pkill -f "camera_server" 2>/dev/null
    pkill -f "vigilancia"    2>/dev/null
    pkill -f "chromium"      2>/dev/null
}
trap stop_all EXIT INT TERM

echo "[Belle] cleaning previous processes..."
pkill -f "uvicorn"       2>/dev/null
pkill -f "telegram_bot"  2>/dev/null
pkill -f "main.py"       2>/dev/null
pkill -f "camera_server" 2>/dev/null
pkill -f "vigilancia"    2>/dev/null
pkill -f "chromium"      2>/dev/null
sleep 2

echo "[Belle] starting internal server..."
uvicorn services.server:app --host 0.0.0.0 --port 8000 >"$LOG_DIR/server.log" 2>&1 &
sleep 3

echo "[Belle] starting Telegram bot..."
python services/telegram_bot.py >"$LOG_DIR/telegram.log" 2>&1 &
sleep 2

# Camera and fall detection with their own environment
echo "[Belle] starting camera..."
venv_camera/bin/python camera/camera_server.py >"$LOG_DIR/camera.log" 2>&1 &
sleep 2

echo "[Belle] starting fall detection..."
venv_camera/bin/python camera/vigilancia.py >"$LOG_DIR/vigilancia.log" 2>&1 &
sleep 2

# Kiosk screen with Chromium
echo "[Belle] starting screen..."
wlr-randr --output HDMI-A-1 --pos 0,0 --scale 2.4
wlr-randr --output DSI-2 --pos 0,0 --scale 1
rm -rf ~/.cache/chromium
DISPLAY=:0 chromium \
    --kiosk \
    --disable-gpu \
    --no-sandbox \
    --disable-dev-shm-usage \
    --disable-software-rasterizer \
    --disable-extensions \
    http://localhost:8000/app >"$LOG_DIR/chromium.log" 2>&1 &
sleep 2

echo "[Belle] starting voice (foreground). Press Ctrl-C to stop everything."
python main.py
