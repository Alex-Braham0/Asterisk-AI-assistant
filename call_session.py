import asyncio
from gemini_client import GeminiClient
from tool_registry import ToolRegistry
from context_builder import ContextBuilder

class CallSession:
    def __init__(self, call, engine, config, db_manager):
        self.call = call
        self.engine = engine
        self.config = config
        self.db_manager = db_manager
        
        self.gemini_client = None
        self.ai_task = None
        self.call_dropped_event = asyncio.Event()
        self.direction = "inbound"
        
        self.tool_registry = ToolRegistry(self)

    async def setup_connection(self, direction="inbound", target_info=None):
        """Pre-warms the Gemini WebSocket connection before audio starts."""
        self.direction = direction

        # Determine caller/target info safely
        if direction == "inbound":
            from_header = self.call.request.headers.get('From', {})
            caller_name = from_header.get('caller', 'Unknown').replace('"', '') # Strip weird quotes
            caller_number = from_header.get('number', 'Unknown')
            caller = f"Name: {caller_name} | Extension: {caller_number}"
        else:
            if isinstance(target_info, dict):
                caller_number = str(target_info.get("extension", target_info.get("target_extension", "Unknown")))
                caller = f"Target: {caller_number} | Context: {target_info.get('context', '')}"
            else:
                caller_number = str(target_info)
                caller = f"Target: {caller_number}"

        caller_tz = self.db_manager.get_user_timezone(caller_number)

        dynamic_prompt = ContextBuilder.build_initial_prompt(
            self.config["system_prompt"], 
            direction=direction, 
            caller_info=caller,
            user_timezone=caller_tz
        )

        self.gemini_client = GeminiClient(
            api_key=self.config["gemini_api_key"],
            pbx_to_ai_queue=self.engine.pbx_to_ai_queue,
            pbx_inject_callback=self.engine.inject_audio,
            pbx_flush_callback=self.engine.flush_tx_buffer,
            tool_handler_callback=self._handle_tool_call,
            system_instruction=dynamic_prompt,
            tools=self.tool_registry.get_declarations()
        )

        try:
            print(f"[CallSession] Pre-warming Gemini WebSocket ({direction})...")
            return await asyncio.wait_for(self.gemini_client.connect(), timeout=10.0)
        except Exception as e:
            print(f"[CallSession] Connection failed: {e}")
            return False

    async def run_bridge(self):
        """Engages the RTP media and AI loops instantly."""
        if not self.call:
            print("[CallSession] Error: Cannot bridge without a valid call object.")
            return

        if self.direction == "inbound":
            self.engine.answer_call(self.call)
        else:
            self.engine.engage_outbound_media()

            if self.gemini_client.is_connected:
                await self.gemini_client.send_system_event(
                    "The human has picked up the phone. Stop waiting. Say 'Hello?' immediately."
                )
            
        self.ai_task = asyncio.create_task(self.gemini_client.run_audio_bridge(
            on_disconnect_callback=self.drop_call
        ))
        monitor_task = asyncio.create_task(self._monitor_call_state())
        
        await self.call_dropped_event.wait()
        
        print("[CallSession] Call hung up by remote party.")
        if self.gemini_client.is_connected:
            await self.trigger_summary(reason="The user has hung up the phone.")
            while self.gemini_client.is_connected:
                await asyncio.sleep(0.1)

        self.terminate_bridge()

    async def _handle_tool_call(self, call_id, name, args):
        # [Keep this exactly as you had it in your uploaded file]
        print(f"[CallSession] Tool Execution Requested: {name}({args})")
        try:
            result = await self.tool_registry.execute_tool(name, args)
        except Exception as e:
            print(f"[CallSession] Tool Execution Exception: {e}")
            result = {"error": "Internal System Error", "details": str(e), "status": "failed"}
        
        if result is not None and self.gemini_client.is_connected:
            await self.gemini_client.send_tool_response(call_id, name, result)

    async def _monitor_call_state(self):
        # [Keep exactly as uploaded]
        try:
            while self.call.state.name == "ANSWERED":
                await asyncio.sleep(0.1)
        finally:
            self.call_dropped_event.set()

    def drop_call(self):
        self.engine.drop_call()

    async def drain_audio_and_drop_call(self):
        """Dynamically waits for the TX buffer to empty, accounting for delayed AI chunks."""
        print("[CallSession] Engaging dynamic audio drain...")
        
        silence_cycles = 0
        # Wait for 1.5 seconds of CONTINUOUS silence (15 consecutive empty cycles)
        while silence_cycles < 15:
            if len(self.engine.tx_buffer) > 0:
                silence_cycles = 0  # Reset the clock! AI sent a late chunk.
            else:
                silence_cycles += 1
            await asyncio.sleep(0.1)
        
        print("[CallSession] Stream continuously silent. Hanging up.")
        self.drop_call()

    async def trigger_summary(self, reason):
        if self.gemini_client and self.gemini_client.is_connected:
            await self.gemini_client.request_summary_and_close(reason)

    def terminate_bridge(self):
        if self.gemini_client: self.gemini_client.is_connected = False
        if self.ai_task: self.ai_task.cancel()

    def cleanup(self):
        print("[CallSession] Cleaning up session resources...")
        self.drop_call()
        self.terminate_bridge()