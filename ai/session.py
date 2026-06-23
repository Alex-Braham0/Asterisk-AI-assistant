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
        self._background_tasks = set()

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
        
        user_data = None

        if endpoint_data:
            default_user_id = endpoint_data.get('default_user_id')
            
            if default_user_id:
                self.active_user_id = default_user_id
                self.active_user_level = endpoint_data.get('default_access_level', 'PRIVATE')
                
                # Fetch raw user dictionary to satisfy the new ContextBuilder schema
                query = "SELECT id, primary_name as name, public_memory, private_memory FROM Users WHERE id = $1"
                async with self.db.pool.acquire() as conn:
                    user_record = await conn.fetchrow(query, default_user_id)
                    
                if user_record:
                    user_data = dict(user_record)
                    # Enforce the dynamic device access level
                    if self.active_user_level == 'BLOCKED':
                        user_data['private_memory'] = "[ACCESS DENIED: Physical device restrictions prevent loading user memory.]"
                    elif self.active_user_level != 'PRIVATE':
                        user_data['private_memory'] = "[PRIVATE MEMORY REDACTED - UNVERIFIED DEVICE LOCATION]"
            else:
                self.active_user_level = 0 
        else:
            self.active_user_level = 0

        # Initialize the updated ContextBuilder class (reads base_prompt natively from config)
        builder = ContextBuilder(self.config)
        dynamic_prompt = builder.build_system_instruction(
            direction=direction,
            caller_info=caller,
            user_data=user_data,
            endpoint_data=endpoint_data
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
                if self.gemini_socket and self.gemini_socket.ws:
                    asyncio.create_task(self.gemini_socket.ws.close())
                return
                
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

    # UPDATE THIS METHOD
    async def _handle_tool_call(self, call_id, name, args):
        print(f"[CallSession] Tool Execution Requested: {name}({args})")
        try:
            result = await self.tool_registry.execute_tool(name, args)
        except Exception as e:
            import traceback
            traceback.print_exc() # Stop hiding crashes!
            result = {"error": "Internal System Error", "details": str(e), "status": "failed"}
        
        if result is not None and self.gemini_socket.is_connected:
            await self.gemini_socket.send_tool_response(call_id, name, result)
        
        self.tools_called.append({"tool": name, "args": args, "response": result})

    def spawn_managed_task(self, coro):
        """Spawns a background task and tracks it for safe teardown."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

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
        
        # Purge all managed background tool tasks
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()

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
        self._background_tasks = set()

    async def execute_mission(self):        
        query = "SELECT id, primary_name as name, public_memory, private_memory FROM Users WHERE id = $1"
        user_data = None
        async with self.db.pool.acquire() as conn:
            user_record = await conn.fetchrow(query, self.active_user_id)
            if user_record:
                user_data = dict(user_record)

        builder = ContextBuilder(self.config)
        system_prompt = builder.build_headless_instruction(
            mission_directive=self.mission_data['mission_directive'],
            user_data=user_data
        )

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

    # ADD THIS MISSING METHOD
    def spawn_managed_task(self, coro):
        """Spawns a background task and tracks it for safe teardown."""
        import asyncio
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    # UPDATE THIS METHOD
    async def _handle_tool_call(self, call_id, name, args):
        print(f"[HeadlessAgent] Tool Execution: {name}({args})")
        try:
            result = await self.tool_registry.execute_tool(name, args)
        except Exception as e:
            import traceback
            traceback.print_exc()  # Stop hiding crashes!
            result = {"error": str(e), "status": "failed"}
            
        if self.gemini_socket.is_connected:
            await self.gemini_socket.send_tool_response(call_id, name, result)