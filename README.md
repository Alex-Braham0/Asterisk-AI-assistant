# 🌟 Winston: Gemini SIP/RTP Voice Agent

[![API Level](https://img.shields.io/badge/API-Gemini%202.5-red.svg)]()
[![Architecture](https://img.shields.io/badge/Architecture-Decoupled%2FAsync-blue.svg)]()

A highly modular, bidirectional voice bridge connecting legacy telephony PBX systems (SIP/RTP) directly to Google's **Gemini 2.5 Flash Native Audio** model via the Live API WebSocket.

This project brings AI conversational intelligence into the traditional telephony world. It allows a user to dial a standard SIP extension and have a natural, low-latency, spoken conversation with an AI agent.

## 🏗️ Architecture & Requirements

The system has been heavily refactored for maintainability, avoiding circular dependencies and "God object" anti-patterns. 

⚠️ **CRITICAL DEPENDENCY:** This script acts as a remote control and audio bridge for **Baresip**. Baresip must be installed, configured, and running on the host machine alongside this script, with the `ctrl_tcp` (Port 4444) and `cons` UDP (Port 5555) modules enabled.

### Directory Structure
* `main.py` - Application entry point.
* `config/` - Strict Pydantic configurations and unified structured logging.
* `core/` - The `SIPAgentOrchestrator` event loop, State Manager (for multi-channel execution), and the Background Scheduler.
* `db/` - `asyncpg` connection pools separated into targeted repositories (`users`, `endpoints`, `tasks`) for safe migrations.
* `telephony/` - Segregated SIP signaling (`baresip_ctrl`) and ALSA/Pulse routing (`audio_bridge`).
* `ai/` - The Gemini WebSocket logic, Context Builder, and downsampling Math.
* `tools/` - Decoupled dynamic toolsets extending agent capability (Weather, Identity Registration, Scheduling).
* `services/` - Independent daemons including `memory_daemon.py` and `pbx_sync.py`.

## 🛠️ Getting Started

### 🚀 Prerequisites

* **Python:** Python 3.11+
* **Baresip:** Must be installed and running on `127.0.0.1` with TCP/UDP control modules enabled.
* **Audio routing:** PulseAudio or ALSA virtual loopback interfaces.
* **PostgreSQL:** Backend persistence for tasks and directory synchronization.

### ⚙️ Installation & Setup

1.  **Clone and Setup:**
    ```bash
    git clone [https://github.com/yourusername/gemini-sip-agent.git](https://github.com/yourusername/gemini-sip-agent.git)
    cd gemini-sip-agent
    ```
2.  **Virtual Environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate
    ```
3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **Generate Configuration:**
    Simply run the application once to generate `config.json`, then fill out your parameters:
    ```bash
    python main.py
    ```

### 📞 Usage

Start the memory compression daemon in the background:
```bash
python services/memory_daemon.py & 
PULSE_SINK=Baresip_Tx PULSE_SOURCE=Baresip_Rx.monitor python main.py