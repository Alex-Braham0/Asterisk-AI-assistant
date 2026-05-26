# 🌟 Winston: Gemini SIP/RTP Voice Agent

[![Code Coverage](https://img.shields.io/badge/Code-Coverage-75%2B-blue.svg)](./coverage_report.html)
[![API Level](https://img.shields.io/badge/API-Gemini%202.5-red.svg)]()
[![Architecture](https://img.shields.io/badge/Architecture-Async%2FStateMachine-blue.svg)]()

A real-time, bidirectional voice bridge connecting legacy telephony PBX systems (SIP/RTP) directly to Google's **Gemini 2.5 Flash Native Audio** model via the Live API WebSocket.

This project brings AI conversational intelligence into the traditional telephony world. It allows a user to dial a standard SIP extension and have a natural, low-latency, spoken conversation with an AI agent capable of:

*   📞 **Native Audio-to-Audio:** Real-time dialogue without relying on slower STT/TTS intermediaries.
*   🧠 **Contextual Memory:** Using vector databases (ChromaDB) to remember key topics and user preferences over multiple calls.
*   🗓️ **Proactive Scheduling:** Scheduling and executing callbacks (e.g., "Remind me to call Dad tomorrow at 5 PM") directly via SIP.
*   🛠️ **Function Calling:** Leveraging advanced Gemini tool-use capabilities (e.g., checking weather, looking up directory numbers).

---

## 🏗️ Architecture Overview (The Big Picture)

The system is a highly modular, Service-Oriented Object Architecture that manages two complex, opposing concurrency domains: the **synchronous 20ms telephony heartbeat** (RTP/SIP) and the **asynchronous WebSocket stream** (Gemini/API).

| Component | File | Primary Responsibility | Interaction Domain |
| :--- | :--- | :--- | :--- |
| **Orchestrator** | `app.py` | The central control loop. Manages the lifecycle of calls, initializes the engine, and runs the background task scheduler. | Orchestration/Async |
| **Media Engine** | `pbx_vocal.py` | The SIP/RTP interface. Manages the background threads, monitors the Baresip event stream, and bridges the physical audio streams (PulseAudio <-> UDP). | Real-time/Audio |
| **Call Session** | `call_session.py` | The state machine. Handles the full lifecycle of a call (setup, run, hangup), manages the handover between the Media Engine and the AI Client. | State Management |
| **Gemini Client** | `gemini_client.py` | The external communication layer. Handles WebSocket connection, audio chunk compression (24kHz -> 8kHz), and continuous streaming to/from Gemini. | Cloud/Networking |
| **Tool Registry** | `tool_registry.py` | The centralized business logic hub. Defines all available functions for Gemini and executes their associated logic (e.g., `check_weather`, `schedule_outbound_call`). | Business Logic/APIs |
| **DB Manager** | `database_manager.py` | The hybrid persistence layer. Uses **SQLite** for deterministic routing (Directory, Tasks) and **ChromaDB** for semantic memory (Context). | Data Persistence |
| **Context Builder**| `context_builder.py`| Generates the initial, highly constrained system prompt used to guide the AI's persona and knowledge during the session. | Prompt Engineering |
| **Sync PBX** | `sync_pbx.py` | Connects to the live FreePBX MySQL database at startup to populate the agent's internal directories and scheduling tables. | Initialization/Sync |

---

## ✨ Key Features

*   **High-Fidelity Conversation:** Direct spoken conversation with minimal latency by using real-time audio streaming (no intermediary STT/TTS latency).
*   **Autonomous Calling:** An asynchronous background worker continuously monitors the database for scheduled tasks (wake-up calls, follow-ups) and initiates outbound calls automatically.
*   **Smart Call Handling:** Detects and responds to non-human sounds (e.g., voicemail greetings) to maintain a seamless experience.
*   **API Integration:** Real-time data fetching (e.g., OpenWeatherMap) that is naturally woven into the conversation.
*   **Guided Conversation:** Contextual prompt generation ensures the AI understands *who* the user is speaking to, *where* they are calling from, and *what* the call's objective is.

## 🛠️ Getting Started

Follow these steps to get the system running.

### 🚀 Prerequisites

*   **Python:** Python 3.11+ is required.
*   **PBX:** A local PBX system (e.g., FreePBX, Asterisk) must be running and accessible.
*   **Database Access:** The script's host IP must have remote read access to the FreePBX MySQL database.
*   **API Keys:** You need a Google Gemini API Key and an OpenWeatherMap API Key.

### ⚙️ Installation & Setup

1.  **Clone and Setup:**
    ```bash
    git clone [https://github.com/yourusername/gemini-sip-agent.git]
    cd gemini-sip-agent
    ```
2.  **Virtual Environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```
3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **Configuration:**
    Create a file named `config.json` in the root directory and populate it with your credentials.
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
    > 💡 **Important:** `my_ip` must be the explicit IPv4 address of the machine running the script.

### 📞 Usage

Start the orchestration loop:

```bash
python app.py
```

The console will confirm the FreePBX directory synchronization, SIP registration, and the background worker activation. Dial the configured extension to begin.

---

## 📅 Roadmap (Future Capabilities)

*   **Agentic Integration:** Connecting to complex, external web services (e.g., OpenClaw, n8n) to allow the AI to interact with smart home devices or complex workflows.
*   **Memory Enhancement:** Implementing a multi-agent pipeline to automatically summarize post-call actions and inject high-value facts into ChromaDB for persistent, long-term memory building.
*   **Scalability:** Refactoring the RTP media engine using `multiprocessing` to handle multiple concurrent calls without GIL bottlenecks.