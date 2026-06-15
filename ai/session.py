import asyncio
import time
import datetime
from ai.gemini_socket import GeminiSocket
from ai.context_builder import ContextBuilder
from tools.registry import ToolRegistry

class CallSession:
    def __init__(self, call, engine, config, db):
        self.call = call
        self.engine = engine
        self.config = config
        self.db = db
        
        self.gemini_socket = None
        self.ai_task = None
        self.call_dropped_event = asyncio.Event()
        self.direction = "inbound"
        
        self.tool_registry = ToolRegistry(self)
        self.tools_called = []

        self.target_extension = "Unknown"
        self.bridge_start_time = None
        self.bridge_start_datetime = None
        self.active_user_id = None
        self.active_user_level = 0

    async def setup_connection(self, direction="inbound", target_info=None):
        """Pre-warms the Gemini WebSocket connection before audio starts."""
        self.direction = direction

        if direction == "inbound":
            from_header = self.call.request.headers.get('From', {})
            caller_name = from_header.get('caller', 'Unknown').replace('"', '')
            caller_number = from_header.get('number', 'Unknown')
            caller = f"Name: {caller_name} | Extension: {caller_number}"
        else:
            if isinstance(target_info, dict):
                caller_number = str(target_info.get("extension", target_info.get("target_extension", "Unknown")))
                caller = f"Target: {caller_number} | Context: {target_info.get('context', '')}"
            else:
                caller_number = str(target_info)
                caller = f"Target: {caller_number}"

        self.target_extension = caller_number

        endpoint_data = await self.db.endpoints.get_endpoint(caller_number)
        
        memory_content = "No specific memory file exists yet."

        if endpoint_data:
            default_user_name = endpoint_data.get('default_user_name')
            if default_user_name:
                self.active_user_level = endpoint_data.get('access_level', 10)
                memory_content = await self.db.users.get_user_memory(default_user_name, self.active_user_level)
            else:
                self.active_user_level = 0 
                memory_content = await self.db.endpoints.get_extension_memory(caller_number)
        else:
            memory_content = await self.db.endpoints.get_extension_memory(caller_number)

        dynamic_prompt = ContextBuilder.build_initial_prompt(
            base_system_prompt=self.config.system_prompt, 
            direction=direction, 
            caller_info=caller,
            endpoint_data=endpoint_data,
            memory_content=memory_content
        )

        self.gemini_socket = GeminiSocket(
            api_key=self.config.gemini_api_key,
            pbx_to_ai_queue=self.engine.pbx_to_ai_queue,
            pbx_inject_callback=self.engine.inject_audio,
            pbx_flush_callback=self.engine.flush_tx_buffer,
            tool_handler_callback=self._handle_tool_call,
            system_instruction=dynamic_prompt,
            tools=self.tool_registry.get_declarations()
        )

        try:
            print(f"[CallSession] Pre-warming Gemini WebSocket ({direction})...")
            return await asyncio.wait_for(self.gemini_socket.connect(), timeout=10.0)
        except Exception as e:
            print(f"[CallSession] Connection failed: {e}")
            return False

    async def run_bridge(self):
        """Engages the RTP media and AI loops instantly."""
        if not self.call:
            print("[CallSession] Error: Cannot bridge without a valid call object.")
            return

        self.bridge_start_time = time.time()
        self.bridge_start_datetime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if self.direction == "inbound":
            await asyncio.sleep(2)
            self.engine.answer_call(self.call)
        else:
            if self.gemini_socket.is_connected:
                await self.gemini_socket.send_system_event(
                    "The human has picked up the phone. Stop waiting. Say 'Hello?' immediately."
                )
            
        self.ai_task = asyncio.create_task(self.gemini_socket.run_audio_bridge(
            on_disconnect_callback=self.drop_call
        ))
        
        monitor_task = asyncio.create_task(self._monitor_call_state())
        
        await self.call_dropped_event.wait()
        
        print("[CallSession] Call hung up by remote party.")
        if self.gemini_socket.is_connected:
            await self.trigger_summary(reason="The user has hung up the phone.")
            
            timeout = 10.0
            start_time = asyncio.get_event_loop().time()
            while self.gemini_socket.is_connected and (asyncio.get_event_loop().time() - start_time) < timeout:
                await asyncio.sleep(0.1)

        self.terminate_bridge()

    async def _handle_tool_call(self, call_id, name, args):
        print(f"[CallSession] Tool Execution Requested: {name}({args})")
        try:
            result = await self.tool_registry.execute_tool(name, args)
        except Exception as e:
            print(f"[CallSession] Tool Execution Exception: {e}")
            result = {"error": "Internal System Error", "details": str(e), "status": "failed"}
        
        if result is not None and self.gemini_socket.is_connected:
            await self.gemini_socket.send_tool_response(call_id, name, result)
        
        self.tools_called.append({"tool": name, "args": args, "response": result})

    async def _monitor_call_state(self):
        try:
            call_id = getattr(self.call, '_id', None)
            # FIX: Corrected to check the single active_call_id tracking variable
            while call_id and call_id == self.engine.active_call_id:
                await asyncio.sleep(0.1)
        finally:
            self.call_dropped_event.set()

    def drop_call(self):
        call_id = getattr(self.call, '_id', None)
        if call_id:
            self.engine.drop_call(call_id)

    async def drain_audio_and_drop_call(self):
        """Drains audio based on API turn status rather than arbitrary silence."""
        print("[CallSession] Engaging dynamic audio drain...")
        
        if self.gemini_socket and self.gemini_socket.is_connected:
            while self.gemini_socket.ai_speaking_event.is_set():
                await asyncio.sleep(0.05)
                
        while len(self.engine.tx_buffer) > 0:
            await asyncio.sleep(0.05)
        
        print("[CallSession] Turn complete and playback stream empty. Hanging up.")
        self.drop_call()

    async def trigger_summary(self, reason):
        if self.gemini_socket and self.gemini_socket.is_connected:
            await self.gemini_socket.request_summary_and_close(reason)

    def terminate_bridge(self):
        if self.gemini_socket: 
            self.gemini_socket.is_connected = False
        if self.ai_task: 
            self.ai_task.cancel()

    def cleanup(self):
        print("[CallSession] Cleaning up session resources...")
        self.drop_call()
        self.terminate_bridge()