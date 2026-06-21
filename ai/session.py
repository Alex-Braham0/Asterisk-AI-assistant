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
        self.direction = direction

        if direction == "inbound":
            from_header = self.call.request.headers.get('From', {})
            caller_name = from_header.get('caller', 'Unknown').replace('"', '')
            caller_number = from_header.get('number', 'Unknown')
            caller = f"Name: {caller_name} | Extension: {caller_number}"
        else:
            caller_number = str(target_info)
            caller = f"Target: {caller_number}"

        self.target_extension = caller_number
        endpoint_data = await self.db.endpoints.get_endpoint(caller_number)
        memory_content = "No specific memory file exists yet."

        if endpoint_data:
            default_user_id = endpoint_data.get('default_user_id')
            
            if default_user_id:
                self.active_user_id = default_user_id
                self.active_user_level = endpoint_data.get('default_access_level', 'PRIVATE')
                # FIX: Pass the exact user_id, not the name
                memory_content = await self.db.users.get_user_memory(default_user_id, self.active_user_level)
            else:
                self.active_user_level = 0 
                memory_content = await self.db.endpoints.get_extension_memory(caller_number)
        else:
            self.active_user_level = 0
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
        if not self.call:
            return

        self.bridge_start_time = time.time()
        self.bridge_start_datetime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if self.direction == "inbound":
            print("[CallSession] Gemini AI initialized. Answering Asterisk line now.")
            
            # 1. Capture the boolean state of the answer attempt
            call_accepted = await self.engine.answer_call()
            
            # 2. STATE-BASED ABORT: If Baresip failed to answer, kill the session instantly.
            if not call_accepted:
                print("[CallSession] ABORT: Call dropped before answering. Terminating AI.")
                self.terminate_bridge()
                return # Exits the function immediately. No AI tasks, no summary loop!
                
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
            result = {"error": "Internal System Error", "details": str(e), "status": "failed"}
        
        if result is not None and self.gemini_socket.is_connected:
            await self.gemini_socket.send_tool_response(call_id, name, result)
        
        self.tools_called.append({"tool": name, "args": args, "response": result})

    async def _monitor_call_state(self):
        try:
            call_id = getattr(self.call, '_id', None)
            while call_id and self.engine.active_call and call_id == getattr(self.engine.active_call, '_id', None):
                await asyncio.sleep(0.1)
        finally:
            self.call_dropped_event.set()

    def drop_call(self):
        call_id = getattr(self.call, '_id', None)
        if call_id:
            self.engine.drop_call()

    async def trigger_summary(self, reason):
        # We only get here if the call successfully connected and ran.
        self.gemini_socket.summary_requested = True
        await self.gemini_socket.send_system_event(f"Requesting final summary. Reason: {reason}")
        
        if self.gemini_socket and self.gemini_socket.is_connected:
            await self.gemini_socket.request_summary_and_close(reason)

    def terminate_bridge(self):
        if self.gemini_socket: 
            self.gemini_socket.is_connected = False
        if self.ai_task: 
            self.ai_task.cancel()

    def cleanup(self):
        self.drop_call()
        self.terminate_bridge()

class HeadlessAgentSession:
    def __init__(self, config, db, engine, mission_data):
        self.config = config
        self.db = db
        self.engine = engine
        self.mission_data = mission_data
        
        self.gemini_socket = None
        self.tool_registry = ToolRegistry(self)  
        
        self.active_user_id = mission_data['owner_user_id']
        self.target_extension = "HEADLESS_AGENT"

    async def execute_mission(self):
        user_memory = await self.db.users.get_user_memory(self.active_user_id, access_level="PRIVATE")
        
        # ai/session.py -> class HeadlessAgentSession -> execute_mission()

        system_prompt = f"""<role_and_identity>
You are a headless, autonomous AI agent executing a background mission. You are NOT currently connected to an audio phone line. You must achieve your objective using your available tools.
</role_and_identity>

<mission_directive>
{self.mission_data['mission_directive']}
</mission_directive>

<user_context>
You are executing this on behalf of User ID: {self.active_user_id}.
{user_memory}
</user_context>

<strict_directives>
1. TRUST PROVIDED NUMBERS: If your mission explicitly includes a phone number (e.g., "extension 6"), use `execute_outbound_dial` immediately.
2. ASYNCHRONOUS DIALING: The `execute_outbound_dial` tool will return immediately while the phone is ringing. You MUST wait silently. Do not generate any spoken text until you receive the "CALL CONNECTED" system event.
3. THE CONVERSATION: When the call connects, you must converse naturally. Do not end the call immediately. 
4. ENDING THE CALL: Once the conversation reaches a natural conclusion, YOU must invoke the `end_call` tool to hang up the line. 
5. COMPLETING THE MISSION: Only AFTER `end_call` has successfully executed, or if the human hangs up on you (notified via system event), you must invoke the `mark_mission_complete` tool to terminate your background session.
</strict_directives>"""

        dummy_queue = asyncio.Queue()
        self.gemini_socket = GeminiSocket(
            api_key=self.config.gemini_api_key,
            pbx_to_ai_queue=dummy_queue,
            pbx_inject_callback=lambda x: None,
            pbx_flush_callback=lambda: None,
            tool_handler_callback=self._handle_tool_call,
            system_instruction=system_prompt,
            tools=self.tool_registry.get_declarations()
        )

        connected = await self.gemini_socket.connect()
        if not connected:
            return False
        
        ai_task = asyncio.create_task(self.gemini_socket.run_audio_bridge(on_disconnect_callback=lambda: None))
        await self.gemini_socket.send_system_event("BEGIN MISSION EXECUTION NOW. Evaluate your tools and take your first step.")
        
        try:
            await asyncio.wait_for(ai_task, timeout=300.0) 
        except asyncio.TimeoutError:
            self.gemini_socket.is_connected = False
            
        return True

    async def _handle_tool_call(self, call_id, name, args):
        print(f"[HeadlessAgent] Tool Execution: {name}({args})")
        try:
            result = await self.tool_registry.execute_tool(name, args)
        except Exception as e:
            result = {"error": str(e), "status": "failed"}
            
        if self.gemini_socket.is_connected:
            await self.gemini_socket.send_tool_response(call_id, name, result)