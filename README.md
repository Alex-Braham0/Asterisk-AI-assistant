# 🌟 Winston: Gemini SIP/RTP Voice Agent

[![API Level](https://img.shields.io/badge/API-Gemini%202.5-red.svg)]()
[![Architecture](https://img.shields.io/badge/Architecture-Async%2FStateMachine-blue.svg)]()

A real-time, bidirectional voice bridge connecting legacy telephony PBX systems (SIP/RTP) directly to Google's **Gemini 2.5 Flash Native Audio** model via the Live API WebSocket.

This project brings AI conversational intelligence into the traditional telephony world. It allows a user to dial a standard SIP extension and have a natural, low-latency, spoken conversation with an AI agent capable of:

* 📞 **Native Audio-to-Audio:** Real-time dialogue without relying on slower STT/TTS intermediaries.
* 🧠 **Contextual Memory:** Uses an autonomous LLM daemon to compress call summaries into flat Markdown files, providing the agent with long-term memory of user preferences.
* 🗓️ **Proactive Scheduling:** Scheduling and executing callbacks (e.g., "Remind me to call Dad tomorrow at 5 PM") directly via SIP.
* 🛠️ **Function Calling:** Leveraging advanced Gemini tool-use capabilities (e.g., checking weather, looking up directory numbers).

---

## 🏗️ Architecture & Requirements

The system is a highly modular architecture that bridges the **synchronous telephony heartbeat** and the **asynchronous WebSocket stream** (Gemini/API).

⚠️ **CRITICAL DEPENDENCY:** This script does *not* handle SIP/RTP packets natively. It acts as a remote control and audio bridge for **Baresip**. Baresip must be installed, configured, and running on the host machine alongside this script, with the `ctrl_tcp` (Port 4444) and `cons` UDP (Port 5555) modules enabled.

| Component | File | Primary Responsibility |
| :--- | :--- | :--- |
| **Orchestrator** | `app.py` | The central `asyncio` loop. Manages call lifecycles and background task scheduling. |
| **Media Engine** | `pbx_vocal.py` | Interfaces with Baresip via TCP/UDP sockets and bridges audio via PulseAudio/ALSA. |
| **Call Session** | `call_session.py` | Handles the state machine of an active call (setup, run, hangup). |
| **Gemini Client** | `gemini_client.py` | Handles the Live API WebSocket connection and audio chunk resampling (24kHz <-> 8kHz). |
| **Tool Registry** | `tool_registry.py` | Defines all available tools (weather, directory, scheduling) for Gemini execution. |
| **DB Manager** | `database_manager.py` | Uses **SQLite** for directory/task routing and **Markdown** files for semantic memory. |
| **Memory Manager**| `memory_manager.py`| An external daemon that processes spooled JSON summaries into concise `.md` profiles. |

---

## 🛠️ Getting Started

### 🚀 Prerequisites

* **Python:** Python 3.11+
* **Baresip:** Must be installed and running on `127.0.0.1` with TCP/UDP control modules enabled.
* **Audio routing:** PulseAudio or ALSA virtual loopback interfaces configured to pipe Baresip's output to the Python script and vice-versa.
* **PBX:** A local PBX system (e.g., FreePBX, Asterisk) accessible over the network.

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
4.  **Configuration:**
    Create `config.json` in the root directory:
    ```json
    {
        "sip_ip": "192.168.1.100", 
        "sip_port": 5060, 
        "my_ip": "192.168.1.98",
        "username": "1001", 
        "password": "your_sip_password",
        "gemini_api_key": "YOUR_GEMINI_API_KEY",
        "openweathermap_api_key": "YOUR_OWM_API_KEY",
        "freepbx_db_ip": "192.168.1.100",
        "freepbx_db_user": "pbxsync",
        "freepbx_db_pass": "your_mysql_password",
        "system_prompt": "You are a helpful phone assistant..."
    }
    ```

### 📞 Usage

Start the memory compression daemon in the background:
```bash
python memory_manager.py & PULSE_SINK=Baresip_Tx PULSE_SOURCE=Baresip_Rx.monitor python app.py
```