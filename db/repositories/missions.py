import asyncpg

class MissionRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def peek_next_mission(self, now_utc) -> bool:
        """Lightweight check to see if pending missions exist without locking rows."""
        query = "SELECT 1 FROM Autonomous_Missions WHERE status = 'pending' AND run_at_utc <= $1 LIMIT 1"
        async with self.pool.acquire() as conn:
            result = await conn.fetchval(query, now_utc)
            return bool(result)

    async def create_mission(self, owner_user_id: int, run_at_utc, directive: str) -> int:
        query = """
            INSERT INTO Autonomous_Missions (owner_user_id, run_at_utc, mission_directive)
            VALUES ($1, $2, $3) RETURNING id
        """
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, owner_user_id, run_at_utc, directive)

    async def get_and_lock_next_mission(self, now_utc) -> dict | None:
        # CRITIQUE FIX: Atomic fetch-and-update prevents race conditions
        query = """
            UPDATE Autonomous_Missions 
            SET status = 'processing' 
            WHERE id = (
                SELECT id FROM Autonomous_Missions 
                WHERE status = 'pending' AND run_at_utc <= $1 
                ORDER BY run_at_utc ASC 
                FOR UPDATE SKIP LOCKED 
                LIMIT 1
            ) RETURNING id, owner_user_id, mission_directive;
        """
        async with self.pool.acquire() as conn:
            record = await conn.fetchrow(query, now_utc)
            return dict(record) if record else None

    async def update_mission_status(self, mission_id: int, status: str, final_report: str = None) -> None:
        if final_report:
            query = "UPDATE Autonomous_Missions SET status = $1, final_report = $2 WHERE id = $3"
            async with self.pool.acquire() as conn:
                await conn.execute(query, status, final_report, mission_id)
        else:
            query = "UPDATE Autonomous_Missions SET status = $1 WHERE id = $2"
            async with self.pool.acquire() as conn:
                await conn.execute(query, status, mission_id)

    async def recover_orphaned_missions(self) -> int:
        """Resets any missions that were stuck in 'processing' during a system crash."""
        query = """
            UPDATE Autonomous_Missions 
            SET status = 'pending' 
            WHERE status = 'processing'
        """
        async with self.pool.acquire() as conn:
            result = await conn.execute(query)
            # asyncpg returns strings like "UPDATE 2"
            return int(result.split()[-1]) if result.startswith("UPDATE") else 0