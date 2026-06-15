import os
import asyncio
import asyncpg

class EndpointRepository:
    def __init__(self, pool: asyncpg.Pool, memory_endpoints_dir: str = "./memory_files/endpoints"):
        self.pool = pool
        self.memory_endpoints_dir = memory_endpoints_dir
        os.makedirs(self.memory_endpoints_dir, exist_ok=True)

    async def get_full_directory(self) -> list[dict]:
        async with self.pool.acquire() as conn:
            records = await conn.fetch('SELECT extension, display_name FROM Endpoints WHERE is_active = TRUE')
            return [dict(r) for r in records]

    async def lookup_extension(self, search_name: str) -> dict | None:
        query = """
            SELECT extension, display_name 
            FROM Endpoints 
            WHERE display_name ILIKE $1 AND is_active = TRUE
            LIMIT 1
        """
        async with self.pool.acquire() as conn:
            record = await conn.fetchrow(query, f"%{search_name}%")
            return dict(record) if record else None

    async def get_endpoint(self, extension: str) -> dict | None:
        query = """
            SELECT e.extension, e.display_name, e.device_type, e.physical_location, e.default_timezone,
                   eu.user_id as default_user_id, eu.access_level as default_access_level, u.primary_name as default_user_name
            FROM Endpoints e
            LEFT JOIN Endpoint_Users eu ON e.extension = eu.extension AND eu.is_default = TRUE
            LEFT JOIN Users u ON eu.user_id = u.id
            WHERE e.extension = $1
        """
        async with self.pool.acquire() as conn:
            record = await conn.fetchrow(query, str(extension))
            return dict(record) if record else None

    async def get_dynamic_access_level(self, extension: str, user_id: int) -> str:
        query = "SELECT access_level FROM Endpoint_Users WHERE extension = $1 AND user_id = $2"
        async with self.pool.acquire() as conn:
            val = await conn.fetchval(query, str(extension), int(user_id))
            if val:
                return val
                
            ep = await self.get_endpoint(extension)
            if ep and ep.get('device_type') == 'STATIC_PRIVATE':
                return 'BLOCKED' 
            return 'SHARED_ONLY'

    async def get_resolved_timezone(self, extension: str) -> str:
        query = """
            SELECT COALESCE(u.current_timezone, e.default_timezone, 'Europe/London')
            FROM Endpoints e
            LEFT JOIN Endpoint_Users eu ON e.extension = eu.extension AND eu.is_default = TRUE
            LEFT JOIN Users u ON eu.user_id = u.id
            WHERE e.extension = $1
        """
        async with self.pool.acquire() as conn:
            val = await conn.fetchval(query, str(extension))
            return val or 'Europe/London'

    async def upsert_endpoint_from_sync(self, extension: str, display_name: str) -> None:
        # Critique 2 Fix: Strictly removes device_type and physical_location parameters from EXCLUDED modifications 
        query = """
            INSERT INTO Endpoints (extension, display_name, is_active)
            VALUES ($1, $2, TRUE)
            ON CONFLICT (extension) 
            DO UPDATE SET 
                display_name = EXCLUDED.display_name,
                is_active = TRUE;
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, str(extension), display_name)

    async def deactivate_missing_endpoints(self, active_extensions: list[str]) -> None:
        if not active_extensions:
            return
        query = "UPDATE Endpoints SET is_active = FALSE WHERE extension != ALL($1::varchar[])"
        async with self.pool.acquire() as conn:
            await conn.execute(query, active_extensions)

    def _read_profile_file(self, filepath: str) -> str:
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()[:2000]
        return "No specific memory data."

    async def get_extension_memory(self, extension: str) -> str:
        filepath = os.path.join(self.memory_endpoints_dir, f"{extension}.md")
        return await asyncio.to_thread(self._read_profile_file, filepath)