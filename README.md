# Gemini SIP/RTP Voice Agent ("Winston")

A real-time, bidirectional voice bridge connecting legacy telephony PBX systems (SIP/RTP) directly to Google's **Gemini 2.5 Flash Native Audio** model via the Live API WebSocket (v1beta). 

This project allows a user to dial a standard SIP extension and have a natural, low-latency, spoken conversation with an AI agent capable of real-time interruption, dynamic tool calling, outbound dialing, and long-term memory.

## 🏗️ Architecture

The system bridges two fundamentally different concurrency domains—synchronous 20ms telephony heartbeats and asynchronous WebSocket bursts—using a modular, Service-Oriented Object Architecture.

* **`app.py` (The Orchestrator):** Bootstraps the application, runs the asynchronous background worker loop for scheduled tasks, and spins up independent session state machines for incoming and outbound calls.
* **`pbx_vocal.py` (The Media Engine):** A wrapper around `pyVoIP`. Manages SIP registration and the strict 20ms background threads required to maintain the RTP UDP stream. 
* **`call_session.py` (The State Machine):** Manages the lifecycle of a single phone call. Negotiates the AI handshake, wires the audio queues, handles pre-warming to eliminate connection latency, and manages disconnections.
* **`gemini_client.py` (The Transport Layer):** Maintains the WebSocket connection to Google. Handles audio downsampling, aliasing prevention, and delegates all JSON tool payloads to the registry.
* **`tool_registry.py` (The Business Logic):** A centralized hub defining the AI's capabilities (e.g., checking weather, scheduling wake-up calls, routing to extensions).
* **`database_manager.py` (The Brain):** Manages a hybrid data layer: SQLite for deterministic routing (Rooms, People, Tasks) and ChromaDB (Vector DB) for semantic context and long-term memory.
* **`context_builder.py` (The Persona):** Dynamically generates context-aware system prompts based on call direction, caller ID, and the scheduled task context.
* **`sync_pbx.py` (The Directory Sync):** Connects directly to the FreePBX MySQL database on boot to automatically map live PBX extensions to the AI's local SQLite routing tables.

## ✨ Current Features

* **Native Audio-to-Audio:** Direct spoken conversation without intermediary Speech-to-Text (STT) or Text-to-Speech (TTS) latency.
* **Proactive Outbound Dialing:** An asynchronous background worker that executes scheduled callbacks and wake-up calls with full context injection.
* **Smart Answering Machine Detection:** Native AI audio analysis allows the agent to instantly detect human pickups vs. voicemail greetings without relying on clunky DSP silence-detection heuristics.
* **Live FreePBX Synchronization:** Seamlessly reads Asterisk extension directories to automatically populate the agent's location and routing tables.
* **API Integrations:** Asynchronous API fetching (via `aiohttp`) for real-time data like OpenWeatherMap forecasts.
* **Voice Activity Detection (VAD):** The AI instantly stops speaking and flushes its transmission buffer if the human interrupts it.
* **Post-Call Summarization:** Automatically forces the AI to output a structured JSON payload of action items and key topics the moment the call ends.

## 🚀 Prerequisites

* **Python 3.11+**
* A local PBX (e.g., FreePBX, Asterisk) with MySQL remote read permissions enabled for the script's host IP.
* A Google Gemini API Key.
* An OpenWeatherMap API Key.

## 🛠️ Installation & Setup

**1. Clone the repository and navigate to the directory:**
```bash
git clone [https://github.com/yourusername/gemini-sip-agent.git](https://github.com/yourusername/gemini-sip-agent.git)
cd gemini-sip-agent
```

**2. Create and activate a virtual environment:**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

**3. Install the required dependencies:**
```bash
pip install -r requirements.txt
```

## ⚙️ Configuration

Create a file named `config.json` in the root directory. Update it with your specific SIP credentials, FreePBX MySQL credentials, and API keys:

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
*(Note: `my_ip` must be the explicit IPv4 address of the machine running the script to prevent FreePBX from interpreting `0.0.0.0` as a SIP hold request).*

## 📞 Usage

Start the orchestrator:

```bash
python app.py
```

You should see the console output indicating the FreePBX directory sync is complete, the SIP extension is registered, and the background task worker is engaged. Dial the configured extension to begin.

## 🗺️ Roadmap / Future Enhancements

* **Agentic Framework Integration:** Implementation of OpenClaw or n8n webhooks to allow the AI to interact with smart home devices, IoT hardware, and complex workflows.
* **Multi-Agent Summary Pipeline:** Piping post-call summaries to a secondary, cheaper text-based LLM to automatically extract facts and inject them into ChromaDB for autonomous long-term memory building.
* **SIP Trunking & Multi-Channel:** Refactoring the RTP media engine with `multiprocessing` to handle multiple concurrent calls without hitting the Python GIL bottleneck.