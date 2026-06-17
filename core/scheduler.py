import asyncio
import datetime
from db.connection import DatabaseConnection
from ai.session import HeadlessAgentSession

class BackgroundScheduler:
    def __init__(self, config, db: DatabaseConnection, orchestrator):
        self.config = config
        self.db = db
        self.orchestrator = orchestrator
        self._is_running = False

    async def run(self) -> None:
        self._is_running = True
        print("[Swarm Worker] Autonomous Mission loop engaged.")
        while self._is_running:
            try:
                # Polite Swarm: Only check the database if the phone line is currently free
                if not self.orchestrator.line_lock.locked():
                    now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                    mission = await self.db.missions.get_and_lock_next_mission(now_utc)
                    
                    if mission:
                        print(f"\n[Swarm Worker] ⚡ Spawning Headless Agent for Mission {mission['id']}")
                        
                        # Claim the line lock so humans can't interrupt the mission
                        async with self.orchestrator.line_lock:
                            try:
                                await self._process_mission(mission)
                            finally:
                                # CRITICAL: Purge stale audio buffers before releasing the lock to the next caller
                                self.orchestrator.engine.flush_tx_buffer()
                                while not self.orchestrator.engine.pbx_to_ai_queue.empty():
                                    try:
                                        self.orchestrator.engine.pbx_to_ai_queue.get_nowait()
                                    except asyncio.QueueEmpty:
                                        break
                                        
            except Exception as e:
                print(f"[Swarm Worker Error] {e}")
                
            await asyncio.sleep(5)

    async def _process_mission(self, mission: dict) -> None:
        try:
            agent = HeadlessAgentSession(self.config, self.db, self.orchestrator.engine, mission)
            await agent.execute_mission()
            await self.db.missions.update_mission_status(mission['id'], 'completed')
            print(f"[Swarm Worker] Mission {mission['id']} completed.")
        except Exception as e:
            print(f"[Swarm Worker Error] Agent crashed for mission {mission['id']}: {e}")
            await self.db.missions.update_mission_status(mission['id'], 'failed')

    def stop(self) -> None:
        self._is_running = False