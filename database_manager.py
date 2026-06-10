import asyncpg
import asyncio
import time
import json
import os
import logging

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, db_url="postgres://postgres:postgres@localhost:5432/asterisk_ai", memory_dir="./memory_files", spool_dir="./call_summaries"):
        self.db_url = db_url
        self.pool = None
        
        self.memory_users_dir = os.path.join(memory_dir, "users")
        self.memory_endpoints_dir = os.path.join(memory_dir, "endpoints")
        os.makedirs(self.memory_users_dir, exist_ok=True)
        os.makedirs(self.memory_endpoints_dir, exist_ok=True)

        self.spool_pending = os.path.join(spool_dir, "pending")
        self.spool_processed = os.path.join(spool_dir, "processed")
        os.makedirs(self.spool_pending, exist_ok=True)
        os.makedirs(self.spool_processed, exist_ok=True)

    async def connect(self):
        if not self.pool:
            self.pool = await asyncpg.create_pool(self.db_url, min_size=1, max_size=10)
            await self._init_sql_tables()

    async def disconnect(self):
        if self.pool:
            await self.pool.close()

    async def _init_sql_tables(self):
        schema = """
        CREATE TABLE IF NOT EXISTS Users (
            id SERIAL PRIMARY KEY,
            primary_name VARCHAR(255) NOT NULL,
            aliases TEXT[] DEFAULT '{}',
            current_timezone VARCHAR(100) DEFAULT 'Europe/London',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS Endpoints (
            extension VARCHAR(50) PRIMARY KEY,
            display_name VARCHAR(255),
            device_type VARCHAR(50) DEFAULT 'STATIC_SHARED', -- STATIC_PRIVATE, STATIC_SHARED, MOBILE
            physical_location VARCHAR(255),
            is_active BOOLEAN DEFAULT TRUE,
            default_timezone VARCHAR(100) DEFAULT 'Europe/London'
        );

        CREATE TABLE IF NOT EXISTS Endpoint_Users (
            extension VARCHAR(50) REFERENCES Endpoints(extension) ON DELETE CASCADE,
            user_id INT REFERENCES Users(id) ON DELETE CASCADE,
            is_default BOOLEAN DEFAULT FALSE,
            access_level VARCHAR(50) DEFAULT 'SHARED_ONLY', -- PRIVATE, SHARED_ONLY, BLOCKED
            PRIMARY KEY (extension, user_id)
        );

        CREATE TABLE IF NOT EXISTS Tasks (
            id SERIAL PRIMARY KEY,
            task_type VARCHAR(100) NOT NULL,
            payload TEXT NOT NULL,
            scheduled_time TIMESTAMP NOT NULL,
            status VARCHAR(50) DEFAULT 'pending'
        );
        """
        async with self.pool.acquire() as conn:
            await conn.execute(schema)

    # --- DIRECTORY & IDENTITY LOGIC ---

    async def get_full_directory(self):
        async with self.pool.acquire() as conn:
            records = await conn.fetch('SELECT extension, display_name FROM Endpoints WHERE is_active = TRUE')
            return [dict(r) for r in records]

    async def lookup_extension(self, search_name):
        query = """
            SELECT extension, display_name 
            FROM Endpoints 
            WHERE display_name ILIKE $1 AND is_active = TRUE
            LIMIT 1
        """
        async with self.pool.acquire() as conn:
            record = await conn.fetchrow(query, f"%{search_name}%")
            return dict(record) if record else None

    async def resolve_users_by_name(self, spoken_name: str):
        """Returns all potential user ID matches based on primary name or aliases."""
        query = """
            SELECT id, primary_name 
            FROM Users 
            WHERE primary_name ILIKE $1 
               OR $1 ILIKE ANY(aliases)
        """
        async with self.pool.acquire() as conn:
            records = await conn.fetch(query, spoken_name)
            return [dict(r) for r in records]

    async def get_user_timezone(self, target_id, is_user=False):
        async with self.pool.acquire() as conn:
            if is_user:
                val = await conn.fetchval("SELECT current_timezone FROM Users WHERE id = $1", int(target_id))
                return val or 'Europe/London'
            else:
                query = """
                    SELECT COALESCE(u.current_timezone, e.default_timezone, 'Europe/London')
                    FROM Endpoints e
                    LEFT JOIN Endpoint_Users eu ON e.extension = eu.extension AND eu.is_default = TRUE
                    LEFT JOIN Users u ON eu.user_id = u.id
                    WHERE e.extension = $1
                """
                val = await conn.fetchval(query, str(target_id))
                return val or 'Europe/London'

    # --- SYNC UPSERTS ---
    
    async def upsert_endpoint(self, extension: str, display_name: str, device_type: str = 'STATIC_SHARED', physical_location: str = None):
        query = """
            INSERT INTO Endpoints (extension, display_name, device_type, physical_location, is_active)
            VALUES ($1, $2, $3, $4, TRUE)
            ON CONFLICT (extension) 
            DO UPDATE SET 
                display_name = EXCLUDED.display_name,
                device_type = EXCLUDED.device_type,
                physical_location = EXCLUDED.physical_location,
                is_active = TRUE;
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, str(extension), display_name, device_type, physical_location)
            
    async def deactivate_missing_endpoints(self, active_extensions: list):
        if not active_extensions: return
        query = "UPDATE Endpoints SET is_active = FALSE WHERE extension != ALL($1::varchar[])"
        async with self.pool.acquire() as conn:
            await conn.execute(query, active_extensions)

    # --- SEMANTIC MEMORY & ACCESS SCOPING ---

    def _read_memory_sync(self, filepath):
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()[:2000]
        return "No specific memory data."

    async def get_endpoint(self, extension):
        """Fetches hardware environment details and the default inhabitant's details."""
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

    async def get_dynamic_access_level(self, extension: str, user_id: int):
        """Calculates the specific access level for a specific user on a specific endpoint."""
        query = "SELECT access_level FROM Endpoint_Users WHERE extension = $1 AND user_id = $2"
        async with self.pool.acquire() as conn:
            val = await conn.fetchval(query, str(extension), int(user_id))
            if val:
                return val
                
            # Fallback implicit logic if no specific DB rule exists
            ep = await self.get_endpoint(extension)
            if ep and ep.get('device_type') == 'STATIC_PRIVATE':
                return 'BLOCKED' # Protect private devices from casual guest intrusion
            return 'SHARED_ONLY'

    async def get_extension_memory(self, extension):
        filepath = os.path.join(self.memory_endpoints_dir, f"{extension}.md")
        return await asyncio.to_thread(self._read_memory_sync, filepath)

    async def get_user_memory(self, user_id: int, access_level: str):
        """Physically enforces memory separation by refusing to read private files without the correct scope."""
        if access_level == 'BLOCKED':
            return "ACCESS DENIED: Physical device restrictions prevent loading user memory."
            
        pub_path = os.path.join(self.memory_users_dir, f"{user_id}_public.md")
        pub_mem = await asyncio.to_thread(self._read_memory_sync, pub_path)
        
        if access_level == 'PRIVATE':
            priv_path = os.path.join(self.memory_users_dir, f"{user_id}_private.md")
            priv_mem = await asyncio.to_thread(self._read_memory_sync, priv_path)
            return f"--- PUBLIC MEMORY ---\n{pub_mem}\n\n--- PRIVATE MEMORY ---\n{priv_mem}"
            
        return f"--- PUBLIC MEMORY ---\n{pub_mem}\n\n[PRIVATE MEMORY REDACTED - UNVERIFIED DEVICE LOCATION]"

    # --- TASK SCHEDULING ---

    async def spool_call_summary(self, extension, summary_data):
        filename = f"{extension}_{int(time.time())}.json"
        filepath = os.path.join(self.spool_pending, filename)
        await asyncio.to_thread(self._write_json, filepath, summary_data)

    def _write_json(self, filepath, data):
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)

    async def schedule_callback(self, target_extension, scheduled_time, payload_dict):
        if isinstance(payload_dict, str):
            payload = json.dumps({"target_extension": target_extension, "context": payload_dict})
        else:
            payload_dict["target_extension"] = target_extension
            payload = json.dumps(payload_dict)
            
        query = "INSERT INTO Tasks (task_type, payload, scheduled_time) VALUES ($1, $2, $3)"
        async with self.pool.acquire() as conn:
            await conn.execute(query, 'outbound_call', payload, scheduled_time)
        return True

    async def get_pending_calls(self, target_extension):
        query = """
            SELECT id, scheduled_time, payload
            FROM Tasks 
            WHERE task_type = 'outbound_call' 
            AND status = 'pending' 
            AND payload LIKE $1
        """
        async with self.pool.acquire() as conn:
            records = await conn.fetch(query, f'%"target_extension": "{target_extension}"%')
            results = []
            for row in records:
                payload_data = json.loads(row['payload'])
                context_str = payload_data.get('context', payload_data.get('execution_context', 'No context provided'))
                results.append({
                    "task_id": row['id'],
                    "scheduled_time": str(row['scheduled_time']),
                    "context": context_str
                })
            return results

    async def cancel_task_by_id(self, task_id):
        query = "UPDATE Tasks SET status = 'cancelled' WHERE id = $1 AND status = 'pending'"
        async with self.pool.acquire() as conn:
            result = await conn.execute(query, int(task_id))
            return int(result.split()[-1])