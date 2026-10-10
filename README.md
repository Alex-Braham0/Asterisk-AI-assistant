# Asterisk-AI-Assistant MVP: Architecture & Deployment Guide

This document details the multi-process architecture, state management, dependencies, and deployment procedures for the SIP-to-Gemini Live Audio Bridge.

## 1. System Architecture

To protect the strict real-time constraints of the SIP audio bridge, the system is split into two isolated processes communicating via Inter-Process Communication (IPC).

* **Asterisk-AI Engine (`main.py`):** Runs the Baresip audio loop, Python Global Interpreter Lock (GIL) management, and the Gemini WebSocket connection.
* **Web Dashboard (`web/app.py`):** A modular FastAPI application serving a real-time HTML/JS status UI and REST API.

### State Management & IPC

* **Live State (RAM Disk IPC):** The engine writes instantaneous state (`in_call`, `idle`, `caller_id`) to a JSON file in the Linux RAM disk (`/dev/shm/asterisk_ai_state.json`) using atomic file replacements. The FastAPI server polls this asynchronously, achieving ~0ms latency with zero disk I/O.
* **Call History (SQLAlchemy ORM):** Completed calls are written to a persistent SQLite database (`data/call_history.db`). The database is managed via SQLAlchemy with Write-Ahead Logging (`PRAGMA journal_mode=WAL`) and Foreign Key constraints enforced at the connection level.

## 2. Dependencies & OS Packages

### System Packages

```bash
sudo apt update
sudo apt install baresip pulseaudio alsa-utils libportaudio2 python3-pip python3-venv sqlite3

```

### Python Requirements

```text
websockets>=12.0
sounddevice>=0.4.6
numpy>=1.26.0
fastapi>=0.103.0
uvicorn[standard]>=0.23.2
sqlalchemy>=2.0.0
alembic>=1.12.0
pydantic>=2.4.0

```

## 3. Database Migrations (Alembic)

The database schema is strictly managed by **Alembic**. You must never modify the database using raw SQL or `create_all()`.

When you add new tables or columns to `core/database.py` (e.g., adding a transcripts table):

1. Generate the migration script: `alembic revision --autogenerate -m "Added transcripts table"`
2. Apply the migration to the database: `alembic upgrade head`

## 4. Deployment & Systemd Configuration

Operating a PulseAudio-backed script headlessly requires explicit user lingering and automated daemon cleanup to prevent file descriptor leaks (`-9993` errors).

1. **Enable User Lingering:** Required for headless PulseAudio.
```bash
sudo loginctl enable-linger gemini
systemctl --user enable --now pulseaudio.socket pulseaudio.service

```

2. **Raise PulseAudio File Descriptor Limit:**
   Prevents PulseAudio from crashing with `-9993` I/O errors due to low default systemd limits (256 FDs).
   ```bash
   mkdir -p ~/.config/systemd/user/pulseaudio.service.d
   echo -e "[Service]\nLimitNOFILE=65536" > ~/.config/systemd/user/pulseaudio.service.d/override.conf
   systemctl --user daemon-reload
   systemctl --user restart pulseaudio


3. **Create Engine Service:** /etc/systemd/system/asterisk-ai.service.
*Note the `ExecStopPost` directive—this guarantees PulseAudio resets if the Python script crashes, preventing lockouts.*

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

# CRITICAL: Clears orphaned PortAudio file descriptors on stop/crash
ExecStopPost=/bin/sh -c 'XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart pulseaudio.service'

StandardOutput=journal
StandardError=journal
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target

```


4. **Create Web Service:** /etc/systemd/system/asterisk-ai-web.service.
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


