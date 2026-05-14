# Gemini SIP/RTP Voice Agent

A real-time, bidirectional voice bridge connecting legacy telephony PBX systems (SIP/RTP) directly to Google's **Gemini 2.5 Flash Native Audio** model via the Live API WebSocket (v1beta). 

This project allows a user to dial a standard SIP extension and have a natural, low-latency, spoken conversation with an AI agent capable of real-time interruption and dynamic tool calling.

## 🏗️ Architecture

The system bridges two fundamentally different concurrency domains—synchronous 20ms telephony heartbeats and asynchronous WebSocket bursts—using a modular, Service-Oriented Object Architecture.

* **`app.py` (The Orchestrator):** Bootstraps the application, manages configuration, and spins up independent session state machines for incoming calls.
* **`pbx_vocal.py` (The Media Engine):** A wrapper around `pyVoIP`. Manages SIP registration and the strict 20ms background threads required to maintain the RTP UDP stream. Converts audio between pyVoIP's 8-bit Unsigned format and standard 16-bit PCM.
* **`call_session.py` (The State Machine):** Manages the lifecycle of a single phone call. Negotiates the AI handshake, wires the audio queues, and handles clean disconnections.
* **`gemini_client.py` (The Transport Layer):** A "dumb" transport layer that maintains the WebSocket connection to Google. Handles audio downsampling (24kHz to 8kHz) and delegates all JSON tool payloads to the registry.
* **`tool_registry.py` (The Business Logic):** A centralized hub defining what the AI is allowed to do. Contains the JSON schema declarations and the Python execution logic for tools like hanging up or summarizing the call.

## ✨ Current Features

* **Native Audio-to-Audio:** Direct spoken conversation without intermediary Speech-to-Text (STT) or Text-to-Speech (TTS) latency.
* **Voice Activity Detection (VAD):** The AI instantly stops speaking and flushes its transmission buffer if the human interrupts it.
* **Tool Calling Execution:** The AI can execute local Python functions. Currently configured to drop the SIP line upon request (`end_call`) and output structured JSON summaries (`submit_call_summary`).
* **Post-Call Summarization:** Automatically forces the AI to output a structured JSON payload of action items and key topics the moment the human caller hangs up.

## 🚀 Prerequisites

* **Python 3.11+** (Python 3.13 is supported, as `audioop` dependencies have been entirely replaced with `numpy`).
* A local PBX (e.g., FreePBX, Asterisk) or a SIP Provider to host the extension.
* A Google Gemini API Key.

## 🛠️ Installation & Setup

**1. Clone the repository and navigate to the directory:**
```bash
git clone [https://github.com/yourusername/gemini-sip-agent.git](https://github.com/yourusername/gemini-sip-agent.git)
cd gemini-sip-agent
```

**2. Create a `.gitignore` file:**
Before making any commits, create a `.gitignore` file in the root directory to ensure you do not accidentally publish your API keys or virtual environment.
```text
# .gitignore
config.json
.env
venv/
env/
.venv/
__pycache__/
*.py[cod]
*$py.class
*.so
.DS_Store
```

**3. Create and activate a virtual environment:**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

**4. Install the required dependencies:**
```bash
pip install pyVoIP websockets numpy
```

## ⚙️ Configuration

Before running the agent, you must configure your network and API details. 

Create a file named `config.json` in the root directory (or run the app once and it will generate a template for you). Update it with your specific SIP credentials and Gemini API key:

```json
{
    "sip_ip": "192.168.1.100", 
    "sip_port": 5060, 
    "username": "1001", 
    "password": "your_sip_password",
    "gemini_api_key": "YOUR_GEMINI_API_KEY",
    "system_prompt": "You are a helpful phone assistant. Give concise answers. Do not narrate your actions."
}
```

## 📞 Usage

Start the orchestrator:

```bash
python app.py
```

You should see the following console output indicating the SIP extension is registered and waiting:

```text
[MediaEngine] Registered SIP account: 1001

--- SIP Agent Online ---
Waiting for calls...
```
Simply dial the configured extension from any softphone or physical SIP handset to begin the conversation.

## 🗺️ Roadmap / Future Enhancements

* **Database Integration:** Implementation of a hybrid SQL/Vector database to maintain user profiles, semantic memories, and entity resolution.
* **SIP Trunking:** Refactoring `pbx_vocal.py` to support multi-channel concurrent calls using a thread-pool.
* **Outbound Call Scheduling:** Allowing the AI to schedule tasks and initiate outbound callbacks to specific users dynamically.