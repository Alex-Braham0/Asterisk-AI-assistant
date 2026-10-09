# Asterisk-AI-Assistant MVP

A low-latency, resilient SIP-to-Gemini Live Audio Bridge. This system acts as a headless SIP endpoint that intercepts phone calls from a PBX (like FreePBX/Asterisk), routes the audio through PulseAudio virtual cables into Python, and streams it bi-directionally to the Google Gemini Live WebSocket API.

## 1. Prerequisites & Dependencies

The system relies on Linux user-space services. It must be run under a dedicated user account (e.g., `gemini`) and requires the following system packages:

```bash
sudo apt update
sudo apt install baresip pulseaudio alsa-utils libportaudio2 python3-pip python3-venv

```

**Python Requirements (`requirements.txt`):**

```text
websockets>=12.0
sounddevice>=0.4.6
numpy>=1.26.0

```

## 2. OS & Audio Configuration (PulseAudio)

To operate headlessly without a desktop environment, PulseAudio must be configured as a persistent background user service.

1. **Enable User Lingering:** Prevents the OS from killing the audio daemon when you disconnect from SSH.
```bash
sudo loginctl enable-linger gemini

```


2. **Disable PulseAudio Idle Timeout:** Prevent the daemon from suspending itself when no calls are active.
```bash
mkdir -p ~/.config/pulse
echo "exit-idle-time = -1" > ~/.config/pulse/daemon.conf

```


3. **Enable the Systemd User Service:**
```bash
systemctl --user daemon-reload
systemctl --user enable --now pulseaudio.service
systemctl --user enable --now pulseaudio.socket

```



## 3. Telephony Configuration (Baresip)

Baresip must be strictly configured to bind to a static SIP port, accept TCP control commands from Python, and route audio through the PulseAudio virtual cables.

### `~/.baresip/config`

Ensure these specific lines are set or uncommented:

```ini
# SIP and Control Networking
sip_listen        0.0.0.0:5060
ctrl_tcp          127.0.0.1:5444

# Audio Routing
audio_player      pulse,Baresip_Rx
audio_source      pulse,Baresip_Tx.monitor

# Required Modules (Ensure these are uncommented in the module block)
module            ctrl_tcp.so
module            pulse.so
module            g711.so
module            stun.so
module            turn.so
module            ice.so

```

### `~/.baresip/accounts`

Register your PBX extension here. Replace `1001`, `password`, and `192.168.1.200` with your PBX credentials.

```text
<sip:1001@192.168.1.200>;auth_pass=password;mediaenc=none

```

## 4. Application Configuration

Create a `config.json` in the root directory of the project. This configures the network and API behavior.

```json
{
    "gemini_api_key": "YOUR_GEMINI_API_KEY",
    "system_prompt": "You are a direct, concise voice assistant on a telephone call. Keep answers short and conversational."
}

```

(Note: Ensure all legacy database and API keys are removed from this file to keep the MVP lean, though standard SIP credentials like `username`, `password`, `sip_ip`, and `sip_port` can remain if utilized by other parts of your pipeline.)

## 5. Deployment & Systemd Service

To ensure the orchestrator starts on boot and restarts on failure, deploy it as a systemd service.

Create the service file: `sudo nano /etc/systemd/system/asterisk-ai.service`

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
ExecStart=/home/gemini/Asterisk-AI-assistant/venv/bin/python -u main.py
StandardOutput=journal
StandardError=journal
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target

```

*(Note: Verify your user ID with `id -u gemini`. If it is not `1000`, adjust the `XDG_RUNTIME_DIR` and `PULSE_SERVER` variables accordingly).*

### Enable and Start the Service

```bash
# Allow the gemini user to view service logs without sudo
sudo usermod -aG systemd-journal gemini

sudo systemctl daemon-reload
sudo systemctl enable --now asterisk-ai
sudo journalctl -u asterisk-ai.service -f

```