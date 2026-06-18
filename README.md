# 🤖 Asterisk AI Assistant (Smart Singleton Architecture)

An enterprise-grade, bidirectional voice AI assistant bridging **Asterisk / FreePBX** with the **Gemini 2.5 Flash Native Audio API**. 

This system acts as a fully autonomous virtual employee. It can answer inbound human calls in real-time with sub-second latency, and when the phone lines are quiet, it spawns **Headless Background Agents** to autonomously execute multi-step database missions and make outbound phone calls.

---

## 🏗️ Architecture: The "Smart Singleton"

Due to the limitations of bare-metal Linux audio threading and PJSIP network collisions, this system utilizes a **Smart Singleton Line-Lock Architecture**. 

Instead of forcing concurrent overlapping SIP softphones (which causes port exhaustion and dropped ACKs), the application manages a single, hyper-stable `baresip` instance.
* **The Traffic Cop:** The `SIPAgentOrchestrator` holds an `asyncio.Lock()` representing the physical SIP extension. 
* **Inbound Priority:** Human callers always get priority. If a human calls, the orchestrator instantly answers the line and bridges the Gemini WebSocket.
* **The Polite Swarm:** A `BackgroundScheduler` continuously polls PostgreSQL for scheduled missions. It will *only* spawn a `HeadlessAgentSession` if the line lock is currently free. 

### Core Modules
* **`telephony/engine.py`**: The OS-level wrapper. Dynamically allocates PulseAudio virtual cables (`Baresip_Tx` / `Baresip_Rx`), boots the Baresip subprocess, and uses `sounddevice` to pipe raw PCM audio into Python.
* **`core/orchestrator.py`**: The state manager. Controls the Line Lock, manages FreePBX ringing events, and flushes stale audio buffers between calls.
* **`ai/session.py`**: The LLM bridge. Handles the Gemini 2.5 WebSocket connection, dynamic system prompting, and the multimodal audio uplink/downlink.
* **`core/scheduler.py`**: The background worker. Safely spawns text-only agents to process the `Autonomous_Missions` database queue.

---

## 🛠️ Tech Stack

* **Language:** Python 3.10+ (Strict `asyncio`)
* **Telephony Stack:** Asterisk / FreePBX -> Baresip (CLI User Agent)
* **Audio Routing:** PulseAudio (`module-null-sink`), `sounddevice`, `PortAudio`
* **AI Provider:** Google Gemini API (Multimodal Live WebSockets)
* **Database:** PostgreSQL (with `asyncpg` for atomic row locking)
* **Process Management:** `systemd`

---

## 📦 Prerequisites

Ensure your bare-metal server (or VM) has the following installed:
* `python3.10` or higher & `python3-venv`
* `postgresql`
* `baresip` (Version 4.x+)
* `pulseaudio` and `pactl` (PulseAudio utilities)
* `libportaudio2` (Required for Python `sounddevice`)

---

## ⚙️ FreePBX / Asterisk Configuration

**CRITICAL:** PJSIP caching will route calls into a blackhole if not configured exactly as follows.
1. Navigate to **Applications -> Extensions** in FreePBX.
2. Edit your AI's target extension (e.g., `1001`).
3. Under the **Advanced** tab, configure the following:
   * **Max Contacts:** `1` *(Prevents Ghost Port collisions)*
   * **Remove Existing:** `Yes` *(Forces Asterisk to respect the Python script on reboot)*
   * **Rewrite Contact:** `Yes` *(Fixes NAT/Local ephemeral port routing)*

---

## 🚀 Installation & Setup

**1. Clone & Environment Setup**
```bash
git clone [https://github.com/yourusername/asterisk-ai-assistant.git](https://github.com/yourusername/asterisk-ai-assistant.git)
cd asterisk-ai-assistant
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**2. Database Initialization**
Ensure PostgreSQL is running. The system will automatically execute schema migrations from `db/migrations.py` on boot, generating the `Users`, `Endpoints`, and `Autonomous_Missions` tables.

**3. Configure Credentials**
Create a `config.env` or update `config/settings.py` with your environment variables:
```env
GEMINI_API_KEY="AIzaSy..."
DB_HOST="localhost"
DB_USER="postgres"
DB_PASS="password"
DB_NAME="asterisk_ai"
SIP_IP="192.168.1.200" # Your PBX IP
```

---

## 💻 Running the Application

### Option A: Manual Development Mode
Run the main telephony engine and swarm manager:
```bash
source venv/bin/activate
python main.py
```
*(In a separate terminal, start the background memory synthesizer)*:
```bash
source venv/bin/activate
python services/memory_daemon.py
```

### Option B: Production Daemonization (`systemd`)
For 24/7 uptime and automatic crash recovery, install the provided systemd services.

1. **Install Services**
Copy the service configurations into systemd (modify user/paths as needed):
```bash
sudo cp systemd/asterisk-ai.service /etc/systemd/system/
sudo cp systemd/asterisk-memory-daemon.service /etc/systemd/system/
```

2. **Enable & Start**
```bash
sudo systemctl daemon-reload
sudo systemctl enable asterisk-ai.service asterisk-memory-daemon.service
sudo systemctl start asterisk-ai.service asterisk-memory-daemon.service
```

3. **View Real-Time Logs**
```bash
sudo journalctl -fu asterisk-ai.service
```

---

## 🔮 Future Development Guidance

If you plan to scale this architecture further, here are the recommended upgrade paths:

### 1. Scaling to Concurrent Calls (Multi-Tenant)
The current limitation is **Baresip / PulseAudio**. You cannot run concurrent overlapping audio streams dynamically through system PulseAudio without heavy ALSA conflicts. To support multiple humans calling the AI at the exact same time:
* **Deprecate Baresip:** Replace `telephony/engine.py` with a native Python SIP/RTP stack (like `aiosip` + `aiortc` or an integration with `Freeswitch`/`Kamailio`).
* **Direct WebRTC/RTP Bridge:** Instead of virtual audio cables, dynamically decode Asterisk's UDP RTP packets into PCM bytearrays natively in Python, keeping the audio entirely inside isolated `asyncio.Queue` objects.

### 2. Advanced Headless Tooling
Currently, the Headless Agent has `execute_outbound_dial`. You can easily expand the swarm's capabilities by adding tools to `tools/registry.py`:
* **`send_email` / `send_sms`**: Allow the agent to text the user if the outbound call fails.
* **`scrape_webpage`**: Allow the agent to read dynamic web content before making a call on behalf of the user.

### 3. Long-Term Vector Memory
Currently, `memory_daemon.py` generates text-based summaries for the system prompt. As the database grows, the prompt will exceed context window efficiency limits.
* **Upgrade:** Implement `pgvector` in PostgreSQL. Chunk the call summaries, generate embeddings, and inject a **RAG (Retrieval-Augmented Generation)** tool so the AI can actively search past conversations rather than having them hardcoded into its boot prompt.