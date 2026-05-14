import asyncio
import sys
import json
from pbx_vocal import MediaEngine
from call_session import CallSession

class SIPAgentOrchestrator:
    def __init__(self, config_path="config.json"):
        try:
            with open(config_path, "r") as f:
                self.config = json.load(f)
        except FileNotFoundError:
            print("[Error] config.json not found. Creating a template and exiting.")
            with open(config_path, "w") as f:
                json.dump({
                    "sip_ip": "192.168.1.100", 
                    "sip_port": 5060, 
                    "username": "100", 
                    "password": "secretpassword",
                    "gemini_api_key": "YOUR_API_KEY",
                    "system_prompt": "You are a helpful phone assistant. Give concise answers."
                }, f, indent=4)
            sys.exit(1)

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # Initialize the SIP Media Engine
        self.engine = MediaEngine(
            self.config["sip_ip"], 
            self.config["sip_port"], 
            self.config["username"], 
            self.config["password"],
            on_call_callback=self._handle_ringing_call
        )

    def start(self):
        self.engine.start()
        print("\n--- SIP Agent Online ---")
        print("Waiting for calls...")
        
        try:
            self.loop.run_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")
            self.engine.stop()
            self.loop.stop()

    def _handle_ringing_call(self, call):
        """Callback triggered by pyVoIP when an incoming call is detected."""
        print(f"\n[App] Call ringing from {call.request.headers['From']['caller']}...")
        
        # Instantiate a new session state machine for this caller
        session = CallSession(call, self.engine, self.config)
        
        # Safely schedule the session negotiation on the main event loop
        asyncio.run_coroutine_threadsafe(session.start(), self.loop)

if __name__ == "__main__":
    app = SIPAgentOrchestrator()
    app.start()