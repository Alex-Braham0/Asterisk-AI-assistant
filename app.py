"PULSE_SINK=Baresip_Tx PULSE_SOURCE=Baresip_Rx.monitor python app.py"

import asyncio
import sys
import json
from pbx_vocal import MediaEngine
from call_session import CallSession
from database_manager import DatabaseManager
from sync_pbx import PBXSynchronizer
import datetime

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
                    "my_ip": "192.168.1.98",
                    "username": "100", 
                    "password": "secretpassword",
                    "gemini_api_key": "YOUR_API_KEY",
                    "openweathermap_api_key": "YOUR_OWM_API_KEY",
                    "freepbx_db_ip": "192.168.1.100",
                    "freepbx_db_user": "pbxsync",
                    "freepbx_db_pass": "your_mysql_password",
                    "postgres_url": "postgres://postgres:YOUR_ACTUAL_PASSWORD@localhost:5432/asterisk_ai",
                    "system_prompt": "You are a helpful phone assistant. Give concise answers."
                }, f, indent=4)
            sys.exit(1)

        # Grab the URL from config, falling back to the default if missing
        pg_url = self.config.get("postgres_url", "postgres://postgres:postgres@localhost:5432/asterisk_ai")
        self.db_manager = DatabaseManager(db_url=pg_url)
        
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        self.engine = MediaEngine(
            self.config["sip_ip"], 
            self.config["sip_port"], 
            self.config["username"], 
            self.config["password"],
            on_call_callback=self._handle_ringing_call,
            my_ip=self.config["my_ip"],
        )

    async def _async_init(self):
        """Asynchronously initializes database and syncs PBX before allowing calls."""
        await self.db_manager.connect()
        synchronizer = PBXSynchronizer(self.config, self.db_manager)
        await synchronizer.run_sync()

    def start(self):
        # Run DB connection and PBX sync inside the event loop before starting engine
        self.loop.run_until_complete(self._async_init())
        
        self.engine.start()
        print("\n--- SIP Agent Online ---")
        print("Waiting for calls...")

        self.loop.create_task(self._background_task_worker())
        
        try:
            self.loop.run_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")
            self.engine.stop()
            self.loop.stop()

    async def _background_task_worker(self):
        """Proactive loop monitoring the database for scheduled tasks."""
        print("[Worker] Background task loop engaged (UTC Timebase).")
        import datetime

        heartbeat_counter = 0

        while True:
            try:
                now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                heartbeat_counter += 1
                
                async with self.db_manager.pool.acquire() as conn:
                    task = await conn.fetchrow('''
                        SELECT id, task_type, payload 
                        FROM Tasks 
                        WHERE status = 'pending' AND scheduled_time <= $1
                        ORDER BY scheduled_time ASC LIMIT 1
                    ''', now_utc)
                    
                    if task:
                        task_id = task['id']
                        task_type = task['task_type']
                        
                        # Set to processing immediately to prevent poison pills
                        await conn.execute("UPDATE Tasks SET status = 'processing' WHERE id = $1", task_id)
                        
                        try:
                            payload = json.loads(task['payload'])
                            print(f"\n[Worker] ⚡ FIRING SCHEDULED TASK {task_id}: {task_type}")
                            
                            if task_type == "outbound_call":
                                target_ext = str(payload.get('target_extension', ''))
                                context = payload.get('context', 'No context provided')
                                priority = payload.get('priority', 'normal')
                                
                                if priority == 'emergency':
                                    context = f"[EMERGENCY OVERRIDE] {context}"
                                
                                print(f"[Worker] Spawning outbound call to {target_ext}. Priority: {priority}")
                                asyncio.create_task(self._initiate_outbound_call(target_ext, context))
                                
                                if payload.get('require_follow_up'):
                                    print(f"[Worker] Task {task_id} requires follow-up. (Logic pending)")
                                    
                            await conn.execute("UPDATE Tasks SET status = 'completed' WHERE id = $1", task_id)
                            
                        except json.JSONDecodeError:
                            print(f"[Worker Error] Task {task_id} has corrupt JSON payload. Marking as failed.")
                            await conn.execute("UPDATE Tasks SET status = 'failed' WHERE id = $1", task_id)
                            
            except Exception as e:
                print(f"[Worker DB Error] {e}")
                
            await asyncio.sleep(5)

    def _handle_ringing_call(self, call):
        """Safely delegates inbound ringing to an async task and catches errors."""
        print(f"\n[App] Call ringing from {call.request.headers['From']['caller']}...")
        future = asyncio.run_coroutine_threadsafe(self._process_inbound_call(call), self.loop)
        
        # Unmask silent asyncio exceptions
        def check_exception(f):
            try:
                f.result()
            except Exception as e:
                import traceback
                print(f"[App Error] Fatal exception in inbound call handler: {e}")
                traceback.print_exc()
                
        future.add_done_callback(check_exception)

    async def _process_inbound_call(self, call):
        """Connects the AI before picking up the receiver."""
        session = CallSession(call, self.engine, self.config, self.db_manager)
        connected = await session.setup_connection(direction="inbound")
        
        if connected:
            await session.run_bridge()
        else:
            try: call.deny() 
            except: pass

    async def _initiate_outbound_call(self, target_extension, context):
        """Initializes AI completely, then dials the SIP line."""
        print(f"[Orchestrator] Pre-warming AI for outbound call to {target_extension}...")
        
        target_info = f"Ext: {target_extension} | Context: {context}"
        
        # 1. Initialize the session WITHOUT a call object yet
        session = CallSession(call=None, engine=self.engine, config=self.config, db_manager=self.db_manager)
        
        # 2. Connect the AI
        connected = await session.setup_connection(direction="outbound", target_info=target_info)
        if not connected:
            print("[Orchestrator] AI setup failed. Aborting outbound call.")
            return

        # 3. NOW initiate the SIP invite (phone starts ringing)
        print(f"[Orchestrator] AI Connected. Dialing {target_extension}...")
        call = self.engine.make_outbound_call(target_extension)
        
        if not call:
            print("[Orchestrator] Aborted: Line busy or error.")
            session.cleanup()
            return
            
        # Attach the live call object to the waiting session
        session.call = call

        # 4. Wait for the human to answer
        timeout_seconds = 45.0
        try:
            answer_task = asyncio.create_task(call.answered_event.wait())
            end_task = asyncio.create_task(call.ended_event.wait())
            
            # Wait for either Answered or Ended, up to 45 seconds
            done, pending = await asyncio.wait(
                [answer_task, end_task],
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # Clean up pending tasks
            for p in pending:
                p.cancel()

            if not done:
                print("[Orchestrator] Outbound call timeout (No Answer).")
                session.cleanup()
                return

            # 5. INSTANT BRIDGE & WAKE UP
            if call.answered_event.is_set():
                print("[Orchestrator] Call answered! Instantly bridging media.")
                await session.run_bridge()
            else:
                print("[Orchestrator] Outbound call failed or rejected.")
                session.cleanup()

        except Exception as e:
            print(f"[Orchestrator Error] {e}")
            session.cleanup()

if __name__ == "__main__":
    app = SIPAgentOrchestrator()
    app.start()