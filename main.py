import os
import sys
import json
import asyncio

# Routing environment variables for PulseAudio
os.environ["PULSE_SINK"] = "Baresip_Tx"
os.environ["PULSE_SOURCE"] = "Baresip_Rx.monitor"

from core.orchestrator import SIPAgentOrchestrator

def load_config(filepath="config.json"):
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[FATAL] Could not load {filepath}: {e}")
        sys.exit(1)

def main():
    config = load_config("config.json")
    
    api_key = config.get("gemini_api_key")
    system_prompt = config.get("system_prompt", "You are a direct, concise voice assistant on a telephone call. Keep answers short and conversational.")
    
    if not api_key:
        print("[FATAL] 'gemini_api_key' not found in config.json")
        sys.exit(1)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    print("[System] Booting Direct SIP-to-Gemini MVP...")
    orchestrator = SIPAgentOrchestrator(api_key=api_key, system_prompt=system_prompt, loop=loop)
    orchestrator.start()

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        print("\n[System] Shutting down gracefully...")
        orchestrator.stop()
        loop.stop()

if __name__ == "__main__":
    main()