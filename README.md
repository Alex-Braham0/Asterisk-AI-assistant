# 🤖 Asterisk AI Assistant (Smart Singleton Architecture)

An enterprise-grade, bidirectional voice AI assistant bridging **Asterisk / FreePBX** with the **Gemini 2.5 Flash Native Audio API**. 

This system acts as a fully autonomous virtual employee. It answers inbound human calls in real-time with sub-second latency. When the phone lines are quiet, it spawns **Headless Background Agents** to autonomously execute multi-step database missions, query external APIs, and make outbound phone calls.

---

## 🏗️ Architecture: The "Smart Singleton"

Due to the limitations of bare-metal Linux audio threading and PJSIP network collisions, this system utilizes a **Smart Singleton Line-Lock Architecture**. 

Instead of forcing concurrent overlapping SIP softphones (which causes port exhaustion and dropped ACKs), the application manages a single, hyper-stable `baresip` instance.
* **The Traffic Cop:** The `SIPAgentOrchestrator` holds an `asyncio.Lock()` representing the physical SIP extension. 
* **Inbound Priority:** Human callers always get priority. If a human calls, the orchestrator instantly answers the line and bridges the Gemini WebSocket.
* **The Polite Swarm:** A `BackgroundScheduler` continuously polls PostgreSQL for scheduled missions. It will *only* spawn a `HeadlessAgentSession` if the line lock is currently free. 
* **Integrated Memory Daemon:** The `DBMemoryDaemon` runs seamlessly as an asynchronous background task within the main loop, digesting call transcripts into condensed public and private vector-style profiles.

### Core Modules
* **`telephony/engine.py`**: The OS-level wrapper. Dynamically allocates PulseAudio virtual cables (`Baresip_Tx` / `Baresip_Rx`), boots the Baresip subprocess, and uses `sounddevice` to pipe raw PCM audio into Python.
* **`core/orchestrator.py`**: The state manager. Controls the Line Lock, manages FreePBX ringing events, and runs the background Swarm/Memory workers.
* **`ai/session.py`**: The LLM bridge. Handles the Gemini 2.5 WebSocket connection, dynamic system prompting, identity resolution, and the multimodal audio uplink/downlink.
* **`tools/registry.py`**: The dynamic toolset. Grants the AI access to identity management, directory search, outbound dialing, and external APIs (e.g., OpenWeatherMap).

---

## 🛠️ Tech Stack

* **Language:** Python 3.10+ (Strict `asyncio`)
* **Telephony Stack:** Asterisk / FreePBX -> Baresip (CLI User Agent)
* **Audio Routing:** PulseAudio (`module-null-sink`), `sounddevice`
* **AI Provider:** Google Gemini API (Multimodal Live WebSockets)
* **Database:** PostgreSQL (with `asyncpg` for atomic row locking)
* **Deployment:** `docker compose`

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

### Option B: Production Daemonization (`systemd`)
For 24/7 uptime and automatic crash recovery, install the provided systemd services.

1. **Install Services**
Copy the service configurations into systemd (modify user/paths as needed):
```bash
sudo cp systemd/asterisk-ai.service /etc/systemd/system/

2. **Enable & Start**
```bash
sudo systemctl daemon-reload
sudo systemctl enable asterisk-ai.service
sudo systemctl start asterisk-ai.service
```

3. **View Real-Time Logs**
```bash
sudo journalctl -fu asterisk-ai.service
```

---