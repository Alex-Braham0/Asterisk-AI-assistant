import json
import os
import tempfile
import sqlite3
import logging
import asyncio
from datetime import datetime
from contextlib import closing

# Environment paths with safe fallbacks
DB_PATH = os.environ.get("DB_PATH", "/opt/asterisk-ai/data/call_history.db")
STATE_FILE = os.environ.get("STATE_FILE", "/dev/shm/asterisk_ai_state.json")

class StateManager:
    def __init__(self):
        self._init_db()
        self.set_live_state_sync(False)

    def _init_db(self):
        os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
        with closing(sqlite3.connect(DB_PATH, timeout=5.0)) as conn:
            with conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        caller_id TEXT,
                        start_time DATETIME,
                        end_time DATETIME,
                        duration INTEGER,
                        status TEXT
                    )
                ''')

    def set_live_state_sync(self, is_active: bool, caller_id: str = None):
        state = {
            "status": "in_call" if is_active else "idle",
            "caller_id": caller_id,
            "timestamp": datetime.utcnow().isoformat()
        }
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=os.path.dirname(STATE_FILE), delete=False) as f:
                temp_path = f.name
                json.dump(state, f)
            os.replace(temp_path, STATE_FILE)
        except Exception as e:
            logging.error(f"UI STATE ERROR (Ignored): Failed to write - {e}")
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    async def set_live_state(self, is_active: bool, caller_id: str = None):
        # Offload the blocking system calls to a thread pool
        await asyncio.to_thread(self.set_live_state_sync, is_active, caller_id)

    def save_call_history(self, caller_id: str, start_time: datetime, end_time: datetime, status: str):
        try:
            duration = int((end_time - start_time).total_seconds())
            with closing(sqlite3.connect(DB_PATH, timeout=5.0)) as conn:
                with conn:
                    conn.execute(
                        "INSERT INTO history (caller_id, start_time, end_time, duration, status) VALUES (?, ?, ?, ?, ?)",
                        (caller_id, start_time.isoformat(), end_time.isoformat(), duration, status)
                    )
        except Exception as e:
            logging.error(f"DB WRITE ERROR (Ignored): Failed to save history - {e}")