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
            name VARCHAR(255) NOT NULL,
            current_timezone VARCHAR(100) DEFAULT 'Europe/London',
            access_level INT DEFAULT 10,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS Endpoints (
            extension VARCHAR(50) PRIMARY KEY,
            display_name VARCHAR(255),
            is_active BOOLEAN DEFAULT TRUE,
            default_timezone VARCHAR(100) DEFAULT 'Europe/London'
        );

        CREATE TABLE IF NOT EXISTS Endpoint_Users (
            extension VARCHAR(50) REFERENCES Endpoints(extension) ON DELETE CASCADE,
            user_id INT REFERENCES Users(id) ON DELETE CASCADE,
            is_default BOOLEAN DEFAULT FALSE,
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

    # --- DIRECTORY LOGIC ---

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

    async def get_user_timezone(self, target_id, is_user=False):
        """Hierarchy: Check User timezone, fallback to Endpoint timezone."""
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
    
    async def upsert_endpoint(self, extension: str, display_name: str):
        query = """
            INSERT INTO Endpoints (extension, display_name, is_active)
            VALUES ($1, $2, TRUE)
            ON CONFLICT (extension) 
            DO UPDATE SET display_name = EXCLUDED.display_name, is_active = TRUE;
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, str(extension), display_name)
            
    async def deactivate_missing_endpoints(self, active_extensions: list):
        if not active_extensions: return
        query = "UPDATE Endpoints SET is_active = FALSE WHERE extension != ALL($1::varchar[])"
        async with self.pool.acquire() as conn:
            await conn.execute(query, active_extensions)

    # --- SEMANTIC MEMORY & TASKS ---

    def _read_memory_sync(self, filepath):
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()[:2000]
        return "No specific memory file exists for this user yet."

    async def spool_call_summary(self, extension, summary_data):
        filename = f"{extension}_{int(time.time())}.json"
        filepath = os.path.join(self.spool_pending, filename)
        await asyncio.to_thread(self._write_json, filepath, summary_data)

    def _write_json(self, filepath, data):
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)

    async def schedule_callback(self, target_extension, scheduled_time, payload_dict):
        # Inject the target into the payload before stringifying
        if isinstance(payload_dict, str):
            # Fallback for legacy calls
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
                # 'context' could be in 'context' or 'execution_context' depending on the tool version used
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
            # asyncpg execute returns string like "UPDATE 1"
            return int(result.split()[-1])
        
    async def get_endpoint(self, extension):
        query = """
            SELECT e.extension, e.display_name, e.default_timezone,
                   eu.user_id, u.name as default_user_name, u.access_level
            FROM Endpoints e
            LEFT JOIN Endpoint_Users eu ON e.extension = eu.extension AND eu.is_default = TRUE
            LEFT JOIN Users u ON eu.user_id = u.id
            WHERE e.extension = $1
        """
        async with self.pool.acquire() as conn:
            record = await conn.fetchrow(query, str(extension))
            return dict(record) if record else None

    async def get_extension_memory(self, extension):
        filepath = os.path.join(self.memory_endpoints_dir, f"{extension}.md")
        return await asyncio.to_thread(self._read_memory_sync, filepath)

    async def get_user_memory(self, user_name):
        filepath = os.path.join(self.memory_users_dir, f"{user_name}.md")
        return await asyncio.to_thread(self._read_memory_sync, filepath)