# Asterisk-AI-Assistant MVP Documentation

This document outlines the architecture, deployment, and configuration for the lean SIP-to-Gemini Live Audio Bridge MVP. The system operates as a single-concurrency orchestrator that intercepts SIP calls, routes raw PCM audio through virtual Linux audio cables, and maintains a low-latency bi-directional WebSocket stream with Google's Gemini AI.

## 1. System Architecture

The application runs as an asynchronous Python loop orchestrating three distinct layers:

1. **Telephony (C-Level):** Baresip acts as the SIP User Agent, locked to port 5060. It handles SIP signaling and RTP media with your PBX (FreePBX/Asterisk).
2. **Audio Routing (OS-Level):** PulseAudio manages two virtual null-sinks (`Baresip_Tx`, `Baresip_Rx`). Python reads from and writes to these sinks via ALSA and `sounddevice`, implementing a 100ms Jitter Buffer to decouple network burstiness from hardware playback.
3. **AI Engine (Network-Level):** A WebSocket connects to the Gemini Live API (`BidiGenerateContent`). It streams 8kHz base64 PCM audio up, and receives 24kHz audio down, decimating it to 8kHz on the fly for PBX compatibility.

## 2. Dependencies

The application requires specific OS-level packages to handle the headless audio routing and SIP termination.

**System Requirements (Linux):**

* `baresip` (v4.8.0+)
* `pulseaudio` (Daemon must be enabled and allowed to run for the executing user)
* `alsa-utils` and `libportaudio2` (Required by the Python `sounddevice` library)

**Python Virtual Environment (`requirements.txt`):**

```text
websockets>=12.0
sounddevice>=0.4.6
numpy>=1.26.0

```

## 3. Configuration

The system relies on two separate configuration domains: the Python application config and the Baresip system config.

### Application Configuration (`config.json`)

The application requires a `config.json` file in the root directory.

```json
{
    "gemini_api_key": "YOUR_GOOGLE_GEMINI_API_KEY",
    "system_prompt": "You are a direct, concise voice assistant on a telephone call. Keep answers short and conversational."
}

```

### Baresip Configuration (`~/.baresip/config`)

The Baresip process must be configured to use PulseAudio and allow TCP control. Ensure the following parameters are strictly set:

* `sip_listen 0.0.0.0:5060` (Locks SIP to a static port so PBX registrations don't ghost).
* `ctrl_tcp 127.0.0.1:5444` (Required for Python to send `/accept` and `/hangup` commands).
* `audio_player pulse,Baresip_Rx`
* `audio_source pulse,Baresip_Tx.monitor`
* Ensure modules `ctrl_tcp.so`, `pulse.so`, and your codecs (`g711.so` / `PCMU`) are loaded.

## 4. Codebase Breakdown

The MVP consists of four isolated files, removing all legacy database, background scheduling, and function-calling logic.

* `main.py`: The launcher. Loads the `config.json` variables, validates the API key, initializes the asyncio event loop, and starts the Orchestrator.
* `core/orchestrator.py`: The traffic cop. Enforces the `asyncio.Lock()` to ensure only one active call. It manages the connection lifecycle: answering the SIP call only *after* the Gemini WebSocket connects, handling graceful hangup events, and ensuring all tasks and buffers are purged on teardown.
* `telephony/engine.py`: The media layer. It auto-heals crashed PulseAudio daemons, allocates the virtual cables, forcefully reboots Baresip zombie processes, and runs the `sounddevice` stream worker. It maintains the 100ms Jitter Buffer to prevent ALSA underruns.
* `ai/gemini_socket.py`: The API bridge. Connects to Gemini's WebSocket. The `_uplink_loop` chunks 8kHz audio into base64 payloads. The `_downlink_loop` decimates incoming 24kHz audio (taking every 3rd sample) and catches `"turnComplete": True` events to allow for graceful system teardowns.

## 5. Deployment & Execution

To run the MVP:

1. Ensure no other service is binding to UDP 5060 or TCP 5444.
2. Activate your virtual environment: `source venv/bin/activate`
3. Execute the launcher: `python main.py`

The system will automatically slaughter existing Baresip instances, refresh the PulseAudio cables, and print `>>> [SYSTEM READY] Listening for inbound calls... <<<`.

