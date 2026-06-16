import asyncio
import datetime
from db.connection import DatabaseConnection
from ai.session import HeadlessAgentSession

class BackgroundScheduler:
    def __init__(self, config, db: DatabaseConnection, pool):
        self.config = config
        self.db = db
        self.pool = pool
        self._is_running = False

    async def run(self) -> None:
        self._is_running = True
        print("[Swarm Worker] Autonomous Mission loop engaged (UTC Timebase).")
        while self._is_running:
            try:
                now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                mission = await self.db.missions.get_and_lock_next_mission(now_utc)
                
                if mission:
                    print(f"\n[Swarm Worker] ⚡ SPAWNING HEADLESS AGENT FOR MISSION {mission['id']}")
                    asyncio.create_task(self._process_mission(mission))
                    
            except Exception as e:
                print(f"[Swarm Worker Error] Internal background exception: {e}")
                
            await asyncio.sleep(5)

    async def _process_mission(self, mission: dict) -> None:
        try:
            agent = HeadlessAgentSession(self.config, self.db, self.pool, mission)
            await agent.execute_mission()
            await self.db.missions.update_mission_status(mission['id'], 'completed')
            print(f"[Swarm Worker] Mission {mission['id']} completed and terminated.")
        except Exception as e:
            print(f"[Swarm Worker Error] Agent execution crashed for mission {mission['id']}: {e}")
            await self.db.missions.update_mission_status(mission['id'], 'failed')

    def stop(self) -> None:
        self._is_running = False