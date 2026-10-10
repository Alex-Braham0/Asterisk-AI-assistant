# Asterisk-AI-Assistant MVP: Architecture & Deployment Guide

This document details the multi-process architecture, state management, dependencies, and deployment procedures for the SIP-to-Gemini Live Audio Bridge.

## 1. System Architecture

To protect the strict real-time constraints of the SIP audio bridge, the system is split into two isolated processes communicating via Inter-Process Communication (IPC). If the web server crashes or experiences heavy load, it will not interrupt an active SIP-to-Gemini phone call.

* **Asterisk-AI Engine (`main.py`):** Runs the Baresip audio loop, Python Global Interpreter Lock (GIL) management, and the Gemini WebSocket connection.
* **Web Dashboard (`web/app.py`):** A FastAPI application serving a real-time HTML/JS status UI via WebSockets.

### State Management (`core/state.py`)

* **Live State (RAM Disk IPC):** The engine writes instantaneous state (`in_call`, `idle`, `caller_id`) to a JSON file in the Linux RAM disk (`/dev/shm/asterisk_ai_state.json`) using atomic file replacements (`os.replace`). The FastAPI server runs a single background task to poll this memory-backed file asynchronously, broadcasting updates to connected browser clients. This achieves ~0ms latency with zero disk I/O or network broker overhead.
* **Call History (SQLite):** Completed calls are written to a persistent SQLite database located inside the project directory (`data/call_history.db`). The database is configured with Write-Ahead Logging (`PRAGMA journal_mode=WAL;`) and native driver timeouts (`timeout=5.0`) to prevent database locking collisions between the engine (writing) and the web dashboard (reading).

## 2. Dependencies & OS Packages

### System Packages

The system relies on Linux user-space audio services and standard build tools.

```bash
sudo apt update
sudo apt install baresip pulseaudio alsa-utils libportaudio2 python3-pip python3-venv sqlite3

```

### Python Requirements (`requirements.txt`)

```text
websockets>=12.0
sounddevice>=0.4.6
numpy>=1.26.0
fastapi>=0.103.0
uvicorn[standard]>=0.23.2

```

*(Note: `uvicorn[standard]` is required over standard `uvicorn` to include the high-performance `httptools` and `websockets` C-extensions.)*

## 3. Deployment & Systemd Configuration

Operating a PulseAudio-backed script headlessly via systemd requires explicit user lingering and environment variable passing.

1. **Enable User Lingering:** Required for headless PulseAudio.
Prevents the OS from terminating the user's PulseAudio daemon when SSH sessions disconnect.

```bash
sudo loginctl enable-linger gemini

```


2. **Enable PulseAudio:** User-space service.
Start the PulseAudio daemon mapped to the specific user.

```bash
systemctl --user daemon-reload
systemctl --user enable --now pulseaudio.socket pulseaudio.service

```


3. **Create Engine Service:** /etc/systemd/system/asterisk-ai.service.
Deploy the core audio bridge. Ensure the `DB_PATH` points to a local `data/` directory.

```ini
[Unit]
Description=Asterisk AI Assistant (Smart Singleton)
After=network.target sound.target

[Service]
Type=simple
User=gemini
Group=gemini
WorkingDirectory=/home/gemini/Asterisk-AI-assistant
Environment="PYTHONUNBUFFERED=1"
Environment="PATH=/home/gemini/Asterisk-AI-assistant/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="XDG_RUNTIME_DIR=/run/user/1000"
Environment="PULSE_SERVER=unix:/run/user/1000/pulse/native"
Environment="DB_PATH=/home/gemini/Asterisk-AI-assistant/data/call_history.db"
Environment="STATE_FILE=/dev/shm/asterisk_ai_state.json"
ExecStart=/home/gemini/Asterisk-AI-assistant/venv/bin/python -u main.py
StandardOutput=journal
StandardError=journal
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target

```


4. **Create Web Service:** /etc/systemd/system/asterisk-ai-web.service.
Deploy the FastAPI dashboard. Binding to `0.0.0.0` allows LAN access, while `127.0.0.1` restricts it to local or reverse-proxy access.

```ini
[Unit]
Description=Asterisk AI Web Dashboard
After=network.target asterisk-ai.service

[Service]
Type=simple
User=gemini
Group=gemini
WorkingDirectory=/home/gemini/Asterisk-AI-assistant/web
Environment="PATH=/home/gemini/Asterisk-AI-assistant/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="DB_PATH=/home/gemini/Asterisk-AI-assistant/data/call_history.db"
Environment="STATE_FILE=/dev/shm/asterisk_ai_state.json"
ExecStart=/home/gemini/Asterisk-AI-assistant/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target

```


5. **Start Services:**
Reload the systemd daemon and activate both services.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now asterisk-ai asterisk-ai-web

```


## 4. Manual Testing (SSH Environment)

When testing via SSH without systemd, Linux does not automatically populate the audio environment variables. You must export them manually to prevent PulseAudio `Connection refused` or ALSA `-9993` errors.

**Terminal 1 (Engine):**

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export PULSE_SERVER=unix:$XDG_RUNTIME_DIR/pulse/native
export DB_PATH=/home/gemini/Asterisk-AI-assistant/data/call_history.db
export STATE_FILE=/dev/shm/asterisk_ai_state.json
python -u main.py

```

**Terminal 2 (Web Server):**

```bash
export DB_PATH=/home/gemini/Asterisk-AI-assistant/data/call_history.db
export STATE_FILE=/dev/shm/asterisk_ai_state.json
uvicorn app:app --host 0.0.0.0 --port 8000 --reload

```

