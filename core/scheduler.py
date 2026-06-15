import asyncio
import datetime
import json
from db.connection import DatabaseConnection

class BackgroundScheduler:
    """
    Critique 4 Fix: Implements programmatic status progression rules.
    Retains status state processing controls until low-level telephony dial
    routines complete execution, avoiding zombie/dropped tasks.
    """
    def __init__(self, db: DatabaseConnection, outbound_callback):
        self.db = db
        self.outbound_callback = outbound_callback
        self._is_running = False

    async def run(self) -> None:
        self._is_running = True
        print("[Worker] Background task loop engaged (UTC Timebase).")
        while self._is_running:
            try:
                now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                async with self.db.pool.acquire() as conn:
                    task = await conn.fetchrow('''
                        SELECT id, task_type, payload 
                        FROM Tasks 
                        WHERE status = 'pending' AND scheduled_time <= $1
                        ORDER BY scheduled_time ASC LIMIT 1
                    ''', now_utc)
                    
                    if task:
                        task_id = task['id']
                        task_type = task['task_type']
                        
                        await conn.execute("UPDATE Tasks SET status = 'processing' WHERE id = $1", task_id)
                        
                        try:
                            payload = json.loads(task['payload'])
                            print(f"\n[Worker] ⚡ FIRING SCHEDULED TASK {task_id}: {task_type}")
                            
                            if task_type == "outbound_call":
                                target_ext = str(payload.get('target_extension', ''))
                                context = payload.get('context', 'No context provided')
                                priority = payload.get('priority', 'normal')
                                
                                if priority == 'emergency':
                                    context = f"[EMERGENCY OVERRIDE] {context}"
                                
                                print(f"[Worker] Spawning outbound call to {target_ext}. Priority: {priority}")
                                
                                success = await self.outbound_callback(target_ext, context)
                                
                                if success:
                                    await conn.execute("UPDATE Tasks SET status = 'completed' WHERE id = $1", task_id)
                                    print(f"[Worker] Task {task_id} successfully dispatched and completed.")
                                else:
                                    await conn.execute("UPDATE Tasks SET status = 'failed' WHERE id = $1", task_id)
                                    print(f"[Worker Error] Outbound call route for task {task_id} failed. Marked failed.")
                                    
                        except json.JSONDecodeError:
                            print(f"[Worker Error] Task {task_id} has corrupted payload schema. Cancelling task entry.")
                            await conn.execute("UPDATE Tasks SET status = 'failed' WHERE id = $1", task_id)
            except Exception as e:
                print(f"[Worker Transaction Failure] Internal background processing exception: {e}")
                
            await asyncio.sleep(5)

    def stop(self) -> None:
        self._is_running = False