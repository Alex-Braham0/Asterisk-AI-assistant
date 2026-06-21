import asyncio
from ai.session import CallSession
from core.scheduler import BackgroundScheduler
from core.state_manager import CallStateManager
from db.connection import DatabaseConnection
from telephony.engine import MediaEngine
from services.memory_daemon import DBMemoryDaemon

class SIPAgentOrchestrator:
    def __init__(self, config, db: DatabaseConnection, loop: asyncio.AbstractEventLoop):
        self.config = config
        self.db = db
        self.loop = loop
        self.state_manager = CallStateManager()
        
        # THE TRAFFIC COP: Ensures only one session uses the phone line at a time
        self.line_lock = asyncio.Lock()
        
        self.engine = MediaEngine(self.config, self.loop, self._handle_inbound_call)
        self.scheduler = BackgroundScheduler(self.config, self.db, self)

        self.memory_daemon = DBMemoryDaemon(self.config, self.db.pool)

    def start(self) -> None:
        print("\n--- Smart Singleton Swarm Online ---")

        recovery_count = self.loop.run_until_complete(self.db.missions.recover_orphaned_missions())
        if recovery_count > 0:
            print(f"[System] Recovered {recovery_count} orphaned background missions from a previous crash.")

        self.engine.start()
        self.loop.create_task(self.scheduler.run())

        self.loop.create_task(self.memory_daemon.run())
        
        try:
            self.loop.run_forever()
        except KeyboardInterrupt:
            self.engine.stop()
            self.scheduler.stop()

            self.loop.run_until_complete(self.db.disconnect())

            self.loop.stop()

    def _handle_inbound_call(self, engine, call) -> None:
        # If a human calls while the AI is executing a background mission, Baresip naturally rejects them.
        # But if the lock is free, we claim it for the human instantly.
        if self.line_lock.locked():
            print("[Orchestrator] Rejected inbound call. AI is currently executing a background mission.")
            engine.drop_call()
            return
            
        future = asyncio.run_coroutine_threadsafe(self._process_inbound_call(engine, call), self.loop)
        future.add_done_callback(lambda f: f.exception() and print(f"App Error: {f.exception()}"))

    async def _process_inbound_call(self, engine, call) -> None:
        async with self.line_lock:
            try:
                session = CallSession(call, engine, self.config, self.db)
                call_id = getattr(call, '_id', None)
                
                if call_id: await self.state_manager.register_session(call_id, session)
                
                connected = await session.setup_connection(direction="inbound")
                if connected:
                    try: 
                        await session.run_bridge()
                    finally: 
                        if call_id: await self.state_manager.unregister_session(call_id)
                else:
                    engine.drop_call()
                    if call_id: await self.state_manager.unregister_session(call_id)
            finally:
                # CRITICAL: Purge stale audio buffers before releasing the lock to the next caller
                engine.flush_tx_buffer()
                while not engine.pbx_to_ai_queue.empty():
                    try:
                        engine.pbx_to_ai_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break