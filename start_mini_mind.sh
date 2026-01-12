#!/bin/bash
# Mini-Mind v2 Startup Script
# Launches virtual display, VNC, Steam/Brotato, and dashboard

echo "=== Mini-Mind v2 Startup ==="

# Kill any existing instances
pkill -f "Xvfb :99" 2>/dev/null
pkill -f "x11vnc -display :99" 2>/dev/null
sleep 1

# Start Xvfb virtual display
echo "[1/5] Starting Xvfb on :99..."
Xvfb :99 -screen 0 1920x1080x24 &
sleep 2

# Start x11vnc with optimized settings (buttery smooth)
echo "[2/5] Starting x11vnc on port 5999 (optimized)..."
x11vnc -display :99 -rfbport 5999 -forever -shared -nopw -xkb \
    -threads -ncache 10 -speeds dsl &
sleep 1

# Start openbox window manager
echo "[3/5] Starting openbox..."
DISPLAY=:99 openbox &
sleep 1

# Launch Steam/Brotato on :99
echo "[4/5] Launching Brotato..."
DISPLAY=:99 steam steam://rungameid/1942280 &
sleep 3

# Start the Command Center dashboard
echo "[5/5] Starting Command Center on port 5001..."
cd /home/vader/mini-mind-v2/workspace/interface
python3 unified_command_center.py &

echo ""
echo "=== Mini-Mind v2 Ready ==="
echo "  VNC:       vnc://localhost:5999 (or port 5999 on LAN)"
echo "  Dashboard: http://localhost:5001"
echo ""
