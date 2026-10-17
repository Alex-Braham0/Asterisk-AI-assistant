# Asterisk-AI-Assistant MVP: Architecture & Deployment Guide
An enterprise-grade, low-latency Audio Bridge connecting Asterisk/Baresip SIP telephony to Google's Gemini Live API (gemini-2.5-flash-native-audio-preview-12-2025 / gemini-3.8-live).

## 1. System Architecture
The project uses a multi-process architecture communicating via Linux RAM Disk Inter-Process Communication (IPC) and SQLite Write-Ahead Logging (WAL).

                      +-----------------------------------+
                      |         Asterisk / PBX            |
                      +-----------------------------------+
                                        | (SIP / RTP)
                                        v
                      +-----------------------------------+
                      |    Baresip (Control Socket 5444)  |
                      +-----------------------------------+
                                        | (PulseAudio Null Sinks)
                                        v
                      +-----------------------------------+
                      |   Asterisk-AI Engine (main.py)    |
                      |   - MediaEngine (Debounce/Fade)   |
                      |   - GeminiSocket (WebSocket Bidi) |
                      |   - Dynamic Tool Registry         |
                      +-----------------------------------+
                             /                     \
      (Atomic RAM Disk IPC) /                       \ (Async SQLite WAL)
                           v                         v
       +-----------------------+         +-----------------------+
       | /dev/shm/state.json   |         |  data/call_history.db |
       +-----------------------+         +-----------------------+
                   |                                 |
                   +----------------+----------------+
                                    |
                                    v
                      +-----------------------------------+
                      |     FastAPI Dashboard & API       |
                      |     (web/app.py - Port 8000)      |
                      +-----------------------------------+

### Key Components
**Media Engine (`telephony/engine.py`)**: Drives Baresip via control socket and processes raw PCM audio using `sounddevice` at 8kHz 16-bit mono. Implements strict PBX debounce delays, DC-offset stripping, and ambient audio fade-ups to stabilize Gemini's native server-side Voice Activity Detection (VAD).

**AI Socket (`ai/gemini_socket.py`)**: Maintains persistent bidirectional WebSockets (`BidiGenerateContent`) with Google's Gemini Live API. Handles 24kHz to 8kHz audio resamplers, uplink flow control, turn-completion triggers, and tool response loops.

**Tool Registry (`ai/tools/registry.py`)**: Decorator-driven function registry that automatically compiles Pydantic schemas into OpenAPI declarations, handles context injection, enforces tool security restrictions, and queues post-turn PBX actions.

**Orchestrator (`core/orchestrator.py`)**: Single-line concurrency lock manager (`line_lock`), state router, prompt compiler, and post-turn action execution coordinator.

## 2. Integrated Tool Suite
The system includes dynamic tools in `ai/tools/functions.py`:
*   `get_current_time`: Returns current time and date formatted with UK timezone support.
*   `get_weather`: Queries the Open-Meteo API for real-time temperature, wind speed, and weather conditions.
*   `transfer_call`: Queues a Baresip SIP REFER transfer to a given extension or user alias, executing safely after audio buffers drain.
*   `toggle_hold`: Sends hold or resume SIP commands to Baresip.
*   `end_call`: Queues a Baresip SIP hangup command, executing safely after speaker audio buffers drain.
*   `call_summary` (restricted=True): SYSTEM ONLY tool. Generates structured JSON call summaries and transcripts saved directly to `data/summaries/`. Locked from unauthorized AI execution during active calls.

## 3. Dependencies & OS Package Setup
**System Packages**
```bash
sudo apt update
sudo apt install -y baresip pulseaudio alsa-utils libportaudio2 python3-pip python3-venv sqlite3
Python Environment (requirements.txt)

Plaintext


websockets>=12.0
sounddevice>=0.4.6
numpy>=1.26.0
fastapi>=0.103.0
uvicorn[standard]>=0.23.2
sqlalchemy>=2.0.0
alembic>=1.12.0
pydantic>=2.4.0
requests>=2.31.0
4. Database Migrations (Alembic)
The SQLite database (data/call_history.db) is managed via Alembic.

Generate a migration script after modifying models in core/database.py:

Bash


alembic revision --autogenerate -m "Add new schema fields"
Apply migrations:

Bash


alembic upgrade head
5. OS & Audio Configuration (Critical Setup)
Step 1: PulseAudio User Service & Lingering

Bash


sudo loginctl enable-linger gemini
systemctl --user enable --now pulseaudio.socket pulseaudio.service
Step 2: Raise Systemd File Descriptor Limits

Bash


mkdir -p ~/.config/systemd/user/pulseaudio.service.d
echo -e "[Service]\nLimitNOFILE=65536" > ~/.config/systemd/user/pulseaudio.service.d/override.conf
systemctl --user daemon-reload
systemctl --user restart pulseaudio
Step 3: Lock Static 8kHz Audio Sinks in /etc/pulse/default.pa

Bash


# Disable idle module suspension
sudo sed -i 's/load-module module-suspend-on-idle/#load-module module-suspend-on-idle/g' /etc/pulse/default.pa

# Append static 8000Hz null-sinks
sudo bash -c 'cat <<EOT>> /etc/pulse/default.pa

# Static Asterisk-AI Virtual Sinks (Locked at 8000Hz Mono)
load-module module-null-sink sink_name=Baresip_Tx format=s16le rate=8000 channels=1 sink_properties=device.description="Baresip_Tx"
load-module module-null-sink sink_name=Baresip_Rx format=s16le rate=8000 channels=1 sink_properties=device.description="Baresip_Rx"
EOT'

systemctl --user restart pulseaudio
Step 4: Disable Baresip Ringtone Modules
To prevent Baresip alert sounds from leaking into audio streams:

Bash


baresip -t 1 & sleep 1; pkill -9 -x baresip
sed -i '/ring\.so/s/^/#/' ~/.baresip/config
sed -i '/ringback\.so/s/^/#/' ~/.baresip/config
6. Systemd Services Setup
Engine Service (/etc/systemd/system/asterisk-ai.service)

Ini, TOML


[Unit]
Description=Asterisk AI Assistant Engine
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
ExecStopPost=/bin/sh -c 'XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart pulseaudio.service'
StandardOutput=journal
StandardError=journal
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
Web Dashboard Service (/etc/systemd/system/asterisk-ai-web.service)

Ini, TOML


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
Enable and Launch

Bash


sudo systemctl daemon-reload
sudo systemctl enable --now asterisk-ai.service asterisk-ai-web.service