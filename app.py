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
                    "username": "100", 
                    "password": "secretpassword",
                    "gemini_api_key": "YOUR_API_KEY",
                    "system_prompt": "You are a helpful phone assistant. Give concise answers."
                }, f, indent=4)
            sys.exit(1)

        self.db_manager = DatabaseManager()

        synchronizer = PBXSynchronizer(self.config, self.db_manager)
        synchronizer.run_sync()

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # Initialize the SIP Media Engine
        self.engine = MediaEngine(
            self.config["sip_ip"], 
            self.config["sip_port"], 
            self.config["username"], 
            self.config["password"],
            on_call_callback=self._handle_ringing_call,
            my_ip=self.config["my_ip"],
        )

    def start(self):
        self.engine.start()
        print("\n--- SIP Agent Online ---")
        print("Waiting for calls...")

        # Start the background worker alongside the main event loop
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
                # 1. Force the current time to pure UTC to match the AI's translation
                now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                
                # 2. Print a visible heartbeat every 10 seconds (every 2nd loop since sleep is 5s)
                if heartbeat_counter % 2 == 0:
                    pass #print(f"[Worker Heartbeat] UTC Time: {now_utc}")
                heartbeat_counter += 1
                
                # Fetch ONE pending task whose time has come
                cursor = self.db_manager.sql_conn.cursor()
                cursor.execute('''
                    SELECT id, task_type, payload 
                    FROM Tasks 
                    WHERE status = 'pending' AND scheduled_time <= ?
                    ORDER BY scheduled_time ASC LIMIT 1
                ''', (now_utc,))
                
                task = cursor.fetchone()
                
                if task:
                    task_id, task_type, payload_str = task['id'], task['task_type'], task['payload']
                    payload = json.loads(payload_str)
                    
                    # Mark as processing to prevent double-execution
                    cursor.execute("UPDATE Tasks SET status = 'processing' WHERE id = ?", (task_id,))
                    self.db_manager.sql_conn.commit()
                    
                    print(f"\n[Worker] ⚡ FIRING SCHEDULED TASK {task_id}: {task_type} at {now_utc}")
                    
                    # Route the task
                    if task_type == "outbound_call":
                        asyncio.create_task(self._initiate_outbound_call(payload['target_extension'], payload['context']))
                    elif task_type == "process_summary":
                        # Hand the summary off to an LLM or vector DB
                        pass 
                        
                    # Mark completed
                    cursor.execute("UPDATE Tasks SET status = 'completed' WHERE id = ?", (task_id,))
                    self.db_manager.sql_conn.commit()
                    
            except Exception as e:
                print(f"[Worker Error] {e}")
                
            # Sleep before checking again (prevents CPU thrashing)
            await asyncio.sleep(5)

    def _handle_ringing_call(self, call):
        """Safely delegates inbound ringing to an async task."""
        print(f"\n[App] Call ringing from {call.request.headers['From']['caller']}...")
        asyncio.run_coroutine_threadsafe(self._process_inbound_call(call), self.loop)

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
        timeout_seconds = 45 
        elapsed = 0
        while call.state.name not in ["ANSWERED", "ENDED", "REJECTED"]:
            await asyncio.sleep(0.1) # Changed from 1 second to 100ms
            elapsed += 0.1
            if elapsed >= timeout_seconds:
                print("[Orchestrator] Outbound call timeout (No Answer).")
                session.cleanup()
                return

        # 5. INSTANT BRIDGE & WAKE UP
        if call.state.name == "ANSWERED":
            print("[Orchestrator] Call answered! Instantly bridging media.")
            await session.run_bridge()
        else:
            print(f"[Orchestrator] Outbound call failed. Final State: {call.state.name}")
            session.cleanup()

if __name__ == "__main__":
    app = SIPAgentOrchestrator()
    app.start()