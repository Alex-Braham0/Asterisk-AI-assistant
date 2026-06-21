import os
import asyncio
import asyncpg

class UserRepository:
    def __init__(self, pool: asyncpg.Pool, memory_users_dir: str = "./memory_files/users"):
        self.pool = pool
        self.memory_users_dir = memory_users_dir
        os.makedirs(self.memory_users_dir, exist_ok=True)

    async def resolve_users_by_name(self, spoken_name: str) -> list[dict]:
        query = """
            SELECT id, primary_name 
            FROM Users 
            WHERE primary_name ILIKE $1 
               OR $1 ILIKE ANY(aliases)
        """
        async with self.pool.acquire() as conn:
            records = await conn.fetch(query, spoken_name)
            return [dict(r) for r in records]

    async def get_user_timezone(self, user_id: int) -> str:
        async with self.pool.acquire() as conn:
            val = await conn.fetchval("SELECT current_timezone FROM Users WHERE id = $1", int(user_id))
            return val or 'Europe/London'

    def _read_profile_file(self, filepath: str) -> str:
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()[:2000]
        return "No existing memory profile."

    async def get_user_memory(self, user_id_or_name: str, access_level: str) -> str:
        if access_level == 'BLOCKED':
            return "ACCESS DENIED: Physical device restrictions prevent loading user memory."
            
        query = "SELECT public_memory, private_memory FROM Users WHERE id = $1"
        async with self.pool.acquire() as conn:
            record = await conn.fetchrow(query, int(user_id_or_name))
            
        if not record:
            return "No existing memory profile."

        if access_level == 'PRIVATE':
            return f"--- PUBLIC MEMORY ---\n{record['public_memory']}\n\n--- PRIVATE MEMORY ---\n{record['private_memory']}"
            
        return f"--- PUBLIC MEMORY ---\n{record['public_memory']}\n\n[PRIVATE MEMORY REDACTED - UNVERIFIED DEVICE LOCATION]"