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

When adding features back into the system (databases, function calling, state management), confine your logic to these specific files.

#### `core/orchestrator.py` (The Integration Hub)

This is your state machine. It is safe to add blocking or asynchronous tasks here, provided they happen *outside* the active audio bridge.

* **Pre-Call Logic (Caller ID, DB Lookups):**
Insert this right after `async with self.line_lock:` but *before* `gemini.connect()`. If you need to look up a user by their phone number in PostgreSQL to customize the `system_prompt`, do it here.
* **Post-Call Logic (Saving Summaries):**
Insert this in the `finally:` block, *after* the `engine.drop_call()` and socket closure. The line lock is still held, meaning the next caller will hear a busy signal until your DB saves are complete. If your DB saves take longer than a few milliseconds, push them to a background `asyncio.create_task()` so the lock releases instantly.

#### `ai/gemini_socket.py` (The AI Capabilities)

This file handles the Gemini Live API protocol. You will modify this heavily to add tools and contextual awareness.

* **Adding Function Calling (Tools):**
Update the `setup_message` dictionary in the `connect()` method to include the `"tools"` array containing your OpenAPI schema definitions.
* **Handling Tool Calls:**
Inside `_downlink_loop`, add a new condition beneath `if "serverContent" in data:`. Look for `if "toolCall" in data:`. You must route the tool arguments to your external functions (e.g., checking FreePBX databases) and immediately send a `toolResponse` payload back to the WebSocket.
* *Warning:* While the AI is waiting for a `toolResponse`, it stops processing audio. Your tool execution must be fast, or the caller will experience dead air.