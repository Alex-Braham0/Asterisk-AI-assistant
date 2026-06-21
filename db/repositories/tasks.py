import os
import time
import json
import asyncio
import asyncpg

class TaskRepository:
    def __init__(self, pool: asyncpg.Pool, spool_dir: str = "./call_summaries"):
        self.pool = pool
        self.spool_pending = os.path.join(spool_dir, "pending")
        os.makedirs(self.spool_pending, exist_ok=True)

    async def schedule_callback(self, target_extension: str, scheduled_time, payload_dict: dict) -> bool:
        payload_dict["target_extension"] = target_extension
        payload = json.dumps(payload_dict)
            
        query = "INSERT INTO Tasks (task_type, payload, scheduled_time) VALUES ($1, $2, $3)"
        async with self.pool.acquire() as conn:
            await conn.execute(query, 'outbound_call', payload, scheduled_time)
        return True

    async def get_pending_calls(self, target_extension: str) -> list[dict]:
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

    async def cancel_task_by_id(self, task_id: int) -> int:
        query = "UPDATE Tasks SET status = 'cancelled' WHERE id = $1 AND status = 'pending'"
        async with self.pool.acquire() as conn:
            result = await conn.execute(query, int(task_id))
            return int(result.split()[-1])

    def _write_spool_file(self, filepath: str, data: dict) -> None:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)

    async def spool_call_summary(self, extension: str, summary_data: dict) -> None:
        payload = json.dumps(summary_data)
        query = "INSERT INTO Tasks (task_type, payload, scheduled_time, status) VALUES ($1, $2, CURRENT_TIMESTAMP, 'pending')"
        async with self.pool.acquire() as conn:
            await conn.execute(query, 'memory_synthesis', payload)