Here is the updated documentation. All original warnings and constraints remain intact, but it now extensively covers the new multi-process architecture, IPC mechanisms, and the safe concurrency patterns we implemented for the web dashboard.

---

# Developer Expansion Guide: SIP-to-Gemini MVP

This guide dictates exactly where to build new features and what underlying infrastructure must remain untouched to prevent regressions in audio latency, network stability, or concurrency locks.

### 1. The "No-Touch" Zones (Core Infrastructure)

These files manage highly sensitive timing loops and OS-level C-bindings. Modifying them risks reintroducing audio judder, ALSA underruns, or frozen SIP ports.

* **`telephony/engine.py` (Strictly Off-Limits):**
* **Why:** The `_stream_worker` runs a real-time hardware audio thread. Python's Global Interpreter Lock (GIL) is aggressively managed here. If you add database calls, heavy logging, or API requests inside this file, you will starve the hardware buffer and instantly cause audio clipping.
* **The Jitter Buffer:** The `1600` byte (100ms) threshold and the starvation logic (`outdata[:] = b'\x00' * req_bytes`) are mathematically tuned to Gemini's network pacing. Do not alter them.


* **Deployment Architecture (Critical Flaw Warning):**
* As noted in previous iterations, tying Baresip to host-level PulseAudio virtual sinks is not a scalable architecture. If you containerize this later using `docker compose`, mapping ALSA sockets into containers requires privileged access and breaks horizontal scaling (you cannot run two instances on the same host). Treat this engine as a temporary MVP bridge until you swap it for a true memory-based RTP library (like `aiortc`).



### 2. The "Extension" Zones (Where to Build)

When adding features back into the system (databases, function calling, state management, UI dashboards), confine your logic to these specific files.

#### `core/orchestrator.py` (The Integration Hub)

This is your state machine. It is safe to add blocking or asynchronous tasks here, provided they happen *outside* the active audio bridge.

* **Pre-Call Logic (Caller ID, DB Lookups):**
Insert this right after `async with self.line_lock:` but *before* `gemini.connect()`. If you need to look up a user by their phone number in PostgreSQL to customize the `system_prompt`, do it here. When updating the UI state (via `state_mgr`), always wrap it in a `try/except` block to ensure UI failures do not drop the SIP call.
* **Post-Call Logic (Saving Summaries & History):**
Insert this in the `finally:` block, *after* the `engine.drop_call()` and socket closure. The line lock is still held, meaning the next caller will hear a busy signal until your DB saves are complete. **Crucial:** To prevent locking out the next caller, push database saves to a background thread using `asyncio.create_task(asyncio.to_thread(...))` so the `line_lock` releases instantly.

#### `core/state.py` (State Management & IPC)

This handles all database persistence and Inter-Process Communication (IPC) between the SIP engine and the Web Dashboard.

* **Live State (RAM Disk IPC):**
To protect the GIL and avoid network overhead, the UI state is passed via the Linux RAM disk (`/dev/shm/asterisk_ai_state.json`). When modifying state writes, you *must* use `tempfile.NamedTemporaryFile` with atomic file swaps (`os.replace`) to prevent the web server from reading partially written JSON.
* **Call History (SQLite Persistence):**
When modifying the database schema or adding new queries, you must strictly follow two rules:
1. **Connection Closures:** The Python `sqlite3` context manager (`with conn:`) *only* handles transactions, it does *not* close the connection. You must wrap connections in `contextlib.closing` to prevent `EMFILE` (Too many open files) crashes.
2. **Driver Locks:** Do not bypass Python's lock handler. Pass timeouts directly to the driver (`sqlite3.connect(..., timeout=5.0)`) and ensure WAL mode (`PRAGMA journal_mode=WAL;`) is active to allow concurrent reading by the web server and writing by the engine.



#### `web/app.py` (The Web Dashboard)

This FastAPI application runs in a completely isolated process to prevent web traffic from impacting audio processing.

* **WebSocket Broadcasting:**
Do not place file polling loops directly inside the WebSocket connection endpoints (this causes an O(N) scaling trap). Always use a singleton `ConnectionManager` and a single background `lifespan` task to poll `/dev/shm` and broadcast to connected clients.
* **Event Loop Blocking:**
When adding new API endpoints that read from SQLite, define them as synchronous functions (`def get_history():` instead of `async def get_history():`). FastAPI will automatically offload synchronous routes to a background `ThreadPoolExecutor`, protecting your WebSocket event loop from database I/O stalls.

#### `ai/gemini_socket.py` (The AI Capabilities)

This file handles the Gemini Live API protocol. You will modify this heavily to add tools and contextual awareness.

* **Adding Function Calling (Tools):**
Update the `setup_message` dictionary in the `connect()` method to include the `"tools"` array containing your OpenAPI schema definitions.
* **Handling Tool Calls:**
Inside `_downlink_loop`, add a new condition beneath `if "serverContent" in data:`. Look for `if "toolCall" in data:`. You must route the tool arguments to your external functions (e.g., checking FreePBX databases) and immediately send a `toolResponse` payload back to the WebSocket.
* *Warning:* While the AI is waiting for a `toolResponse`, it stops processing audio. Your tool execution must be fast, or the caller will experience dead air.