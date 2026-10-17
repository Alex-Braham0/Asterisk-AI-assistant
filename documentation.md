---

### Updated Developer Expansion Guide

```markdown
# Developer Expansion Guide: SIP-to-Gemini MVP

This guide dictates exactly where to build new features and what underlying infrastructure must remain untouched to prevent regressions in audio latency, network stability, or concurrency locks.

## 1. The "No-Touch" Zones (Core Infrastructure)

These files manage highly sensitive timing loops and OS-level C-bindings. Modifying them risks reintroducing audio judder, ALSA underruns, hallucinations, or frozen SIP ports.

### `telephony/engine.py` (Strictly Off-Limits)
- **The Hardware Pipe:** The `_stream_worker` runs a real-time hardware audio thread. Python's GIL is aggressively managed here. Do NOT add database calls, heavy logging, or API requests inside this callback thread; doing so will starve the hardware buffer and instantly cause audio clipping.
- **Low-Latency Hardware Audio:** The engine uses `sounddevice.RawStream` configured with `blocksize=320` (40ms chunks at 8kHz PCM).
- **PBX Debounce, DC Offset Removal & Ambient Fade-Up:** The engine manages a delicate handoff to Gemini's hyper-sensitive server-side VAD. The microphone is strictly hard-muted (`b'\x00'`) while the AI delivers its initial greeting. Upon the AI finishing (`turnComplete`), `ungate_mic()` triggers a precise sequence: an 800ms debounce of absolute silence (to swallow delayed PBX connection beeps), followed by DC offset removal (`mic_audio -= np.mean(mic_audio)`), and finally a 1.0-second linear amplitude fade-up. This prevents sudden volume jumps or artificial low-frequency waves that cause the AI to hallucinate speech and interrupt itself.
- **Audio Buffer Draining:** The engine automatically pads and drains incomplete frame chunks so the output buffer cleanly reaches `len(self.tx_buffer) == 0`. This enables post-turn execution tasks to detect exact speech completion.
- **The Anti-Lag Jitter Buffer:** The `pbx_to_ai_queue` is strictly capped at `maxsize=10` (~400ms of audio). If network latency stutters, Python drops old microphone packets instead of trapping the caller in a compounding audio delay loop.
- **Static Audio Routing:** `Baresip_Tx` and `Baresip_Rx` are locked to `s16le 1ch 8000Hz` as permanent static null-sinks inside the host's `/etc/pulse/default.pa`. Never attempt to run `pactl load-module` from within the Python codebase.

## 2. The "Extension" Zones (Where to Build)

When adding features back into the system (databases, function calling, state management, UI dashboards), confine your logic to these specific files.

### `core/orchestrator.py` (The Integration Hub)
This is your state machine. It handles call lifecycle events, dynamic prompt compilation, and post-turn queue execution.
- **Pre-Call Logic & Dynamic Context:** Context is built before WebSocket setup via `PromptManager.build_prompt(call_context)`. Injected dynamic variables include caller name, caller extension number, current time, and UK timezone offset from root `config.json`.
- **Socket Initialization:** The Orchestrator maps the media engine's `ungate_mic` callback into the `GeminiSocket` so the audio stream can dynamically react to server-side `turnComplete` events.
- **Post-Turn Task Callbacks:** Terminal actions like `transfer_call` and `end_call` are wrapped in buffer-draining async tasks. These tasks monitor `len(engine.tx_buffer) == 0`, apply a 400ms physical echo-tail hold, and execute the PBX command after the speaker audio has completely finished playing out to the caller.
- **Post-Call Summaries:** When the caller hangs up, `gemini.audio_uplink_active = False` is set immediately to stop microphone packets from colliding with text commands on the WebSocket. The orchestrator unlocks restricted tools (`call_context["restricted_unlocked"] = True`) and sends a system event prompting `call_summary`. DB saves are offloaded to background tasks to release `line_lock` instantly.

### `ai/tools/registry.py` & `ai/tools/functions.py` (Tool Capabilities & Security)
Tool declarations use a dynamic decorator-based registry (`ToolRegistry`).
- **Decorator Registration:** Decorate functions using `@registry.register(...)`. Parameters must be defined using Pydantic BaseModel schemas. The registry automatically parses type hints, descriptions, nested models, and arrays into OpenAPI-compliant `functionDeclarations` for Gemini's setup payload.
- **Context Injection:** If a function parameter is named `context: dict`, the registry automatically injects the active call's runtime state dictionary without exposing context in the OpenAPI schema sent to Gemini.
- **Restricted Tool Security (`restricted=True`):** Tools registered with `restricted=True` (e.g., `call_summary`) cannot be executed directly by the AI. If the AI attempts to call a restricted tool prematurely, `ToolRegistry.execute()` intercepts the request and returns a SYSTEM DENIED error. Restricted tools can only be run after the orchestrator explicitly sets `context["restricted_unlocked"] = True` during an authorized system event.
- **Post-Turn Action Queuing:** Terminal tools (`transfer_call`, `end_call`) must not execute Baresip commands directly inside the tool function. Instead, they schedule their execution callback in `context["post_turn_actions"]`. The socket triggers these queued tasks upon receiving Gemini's `turnComplete` signal.

### `core/prompt.py` (Prompt & Context Management)
- **Root Configuration:** `PromptManager` loads the base `system_prompt` from root `config.json`.
- **Live Injection:** `build_prompt(call_context)` appends live metadata blocks (caller ID, extension, current UK time/date) to the system instruction payload during `GeminiSocket` initialization.

### `core/database.py` & `core/schemas.py` (Data Layer)
- **Alembic Migrations:** Never edit the SQLite schema directly or use `Base.metadata.create_all()`. Schema updates strictly require Alembic migrations (`alembic revision --autogenerate -m "description"` followed by `alembic upgrade head`).
- **Schema Immutability:** The core `calls` table is immutable. Structured transcripts (`List[TranscriptLine]`) and tool logs are saved as JSON payloads linked to call entries.
- **SQLite Concurrency:** SQLite uses Write-Ahead Logging (`PRAGMA journal_mode=WAL`) with `aiosqlite` and `AsyncSession` to prevent `sqlite3.OperationalError: database is locked`.

### `core/state.py` (Live State & IPC)
- **Live State (RAM Disk IPC):** Instantaneous state (`in_call`, `idle`, `caller_id`) is written to `/dev/shm/asterisk_ai_state.json` using `tempfile.NamedTemporaryFile` and atomic file swaps (`os.replace`) to prevent partially read JSON files in the web server.

### `web/routers/` (Web Dashboard & API)
- **Modular Endpoints:** New routes go into `web/routers/` and mount onto `app.py`.
- **WebSocket Rules:** Clients connect to `/ws/` using the singleton `ConnectionManager`. The WebSocket handler reads state from `/dev/shm/asterisk_ai_state.json`.