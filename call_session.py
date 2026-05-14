import asyncio
from gemini_client import GeminiClient
from tool_registry import ToolRegistry

class CallSession:
    def __init__(self, call, engine, config):
        self.call = call
        self.engine = engine
        self.config = config
        
        self.gemini_client = None
        self.ai_task = None
        
        # Instantiate the business logic for this specific session
        self.tool_registry = ToolRegistry(self)

    async def start(self):
        """Negotiates the AI connection and begins the audio bridge."""
        self.gemini_client = GeminiClient(
            api_key=self.config["gemini_api_key"],
            pbx_to_ai_queue=self.engine.pbx_to_ai_queue,
            pbx_inject_callback=self.engine.inject_audio,
            pbx_flush_callback=self.engine.flush_tx_buffer,
            tool_handler_callback=self._handle_tool_call,
            system_instruction=self.config["system_prompt"],
            tools=self.tool_registry.get_declarations()
        )

        def safe_deny():
            try: self.call.deny()
            except Exception: pass

        try:
            print(f"[CallSession] Attempting Gemini WebSocket handshake for {self.call.request.headers['From']['caller']}...")
            connected = await asyncio.wait_for(self.gemini_client.connect(), timeout=5.0)
            
            if connected:
                self.engine.answer_call(self.call)
                self.ai_task = asyncio.create_task(self.gemini_client.run_audio_bridge())
                
                # Keep task alive while the call is active
                while self.call.state.name == "ANSWERED":
                    await asyncio.sleep(0.5)
                
                print("[CallSession] Call hung up by remote party.")

                # Interject and request the summary before closing the bridge
                if self.gemini_client.is_connected:
                    await self.trigger_summary(reason="The user has hung up the phone.")
                    
                    while self.gemini_client.is_connected:
                        await asyncio.sleep(0.1)

                self.terminate_bridge()
            else:
                print("[CallSession] Gemini handshake failed. Rejecting call.")
                safe_deny()
                
        except asyncio.TimeoutError:
            print("[CallSession] Gemini API timeout. Rejecting call.")
            safe_deny()
        except Exception as e:
            print(f"[CallSession] Exception during negotiation: {e}")
            safe_deny()
            self.cleanup()

    async def _handle_tool_call(self, call_id, name, args):
        """Passes tool requests to the registry and routes the response back to Gemini."""
        print(f"[CallSession] Tool Execution Requested: {name}({args})")
        result = await self.tool_registry.execute_tool(name, args)
        
        if result is not None and self.gemini_client.is_connected:
            await self.gemini_client.send_tool_response(call_id, name, result)

    def drop_call(self):
        """Drops the SIP line."""
        self.engine.drop_call()

    async def trigger_summary(self, reason):
        """Injects the command to force the AI to summarize."""
        if self.gemini_client and self.gemini_client.is_connected:
            await self.gemini_client.request_summary_and_close(reason)

    def terminate_bridge(self):
        """Kills the WebSocket connection and async tasks."""
        if self.gemini_client:
            self.gemini_client.is_connected = False
        if self.ai_task:
            self.ai_task.cancel()

    def cleanup(self):
        """Emergency cleanup method."""
        print("[CallSession] Cleaning up session resources...")
        self.drop_call()
        self.terminate_bridge()