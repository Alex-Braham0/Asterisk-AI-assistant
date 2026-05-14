import asyncio
import sys
import json
from pbx_vocal import MediaEngine
from gemini_client import GeminiClient

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

        # Establish the primary asyncio event loop for WebSocket management
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
        
        self.current_ai_task = None
        self.gemini_client = None

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
        
        # Safely schedule the async negotiation task from the pyVoIP thread to the main event loop
        asyncio.run_coroutine_threadsafe(
            self._negotiate_and_answer(call),
            self.loop
        )

    async def _negotiate_and_answer(self, call):
        """Establishes the Gemini WebSocket connection before answering the SIP call."""
        self.gemini_client = GeminiClient(
            api_key=self.config["gemini_api_key"],
            pbx_to_ai_queue=self.engine.pbx_to_ai_queue,
            pbx_inject_callback=self.engine.inject_audio,
            pbx_flush_callback=self.engine.flush_tx_buffer,
            pbx_drop_call_callback=self.engine.drop_call,
            system_instruction=self.config["system_prompt"]
        )

        def safe_deny():
            """Suppresses pyVoIP's internal exceptions if a call is denied before RTP initializes."""
            try:
                call.deny()
            except AttributeError:
                pass 
            except Exception:
                pass

        try:
            print("[App] Attempting Gemini WebSocket handshake...")
            connected = await asyncio.wait_for(self.gemini_client.connect(), timeout=5.0)
            
            if connected:
                self.engine.answer_call(call)
                self.current_ai_task = asyncio.create_task(self.gemini_client.run_audio_bridge())
                
                # Keep task alive while the call is active
                while call.state.name == "ANSWERED":
                    await asyncio.sleep(0.5)
                
                print("[App] Call hung up by remote party.")

                # Interject and request the summary before closing the bridge
                if self.gemini_client.is_connected:
                    await self.gemini_client.request_summary_and_close(
                        reason="The user has hung up the phone."
                    )
                    
                    # Keep the orchestrator waiting until the AI finishes generating the tool payload
                    while self.gemini_client.is_connected:
                        await asyncio.sleep(0.1)

                # Force the websocket loops to collapse immediately
                self.gemini_client.is_connected = False 
                if self.current_ai_task:
                    self.current_ai_task.cancel()
            else:
                print("[App] Gemini handshake failed. Rejecting call.")
                safe_deny()
                
        except asyncio.TimeoutError:
            print("[App] Gemini API timeout. Rejecting call.")
            safe_deny()
        except Exception as e:
            print(f"[App] Exception during negotiation: {e}")
            safe_deny()
        finally:
            self._cleanup()

    def _cleanup(self):
        print("[App] Cleaning up resources...")
        self.engine.drop_call()
        if self.current_ai_task:
            self.current_ai_task.cancel()
        if self.gemini_client:
            self.gemini_client.is_connected = False

if __name__ == "__main__":
    app = SIPAgentOrchestrator()
    app.start()