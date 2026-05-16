import sqlite3
import chromadb
import time
import json

class DatabaseManager:
    def __init__(self, sqlite_path="pbx_core.db", chroma_path="./chroma_data"):
        self.sql_conn = sqlite3.connect(sqlite_path, check_same_thread=False)
        self.sql_conn.row_factory = sqlite3.Row
        self._init_sql_tables()

        self.chroma_client = chromadb.PersistentClient(path=chroma_path)
        self.pref_collection = self.chroma_client.get_or_create_collection(name="home_context")

    def _init_sql_tables(self):
        """Creates the flat Directory schema and the Task Queue."""
        cursor = self.sql_conn.cursor()
        
        # A single flat Directory table mirroring FreePBX
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Directory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                extension TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                timezone TEXT DEFAULT 'Europe/London'
            )
        ''')

        # The Unified Task Queue
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                scheduled_time DATETIME NOT NULL,
                status TEXT DEFAULT 'pending'
            )
        ''')
        self.sql_conn.commit()

    # --- DIRECTORY LOGIC ---

    def get_full_directory(self):
        """Returns the entire phonebook."""
        cursor = self.sql_conn.cursor()
        cursor.execute('SELECT extension, display_name FROM Directory')
        return [dict(row) for row in cursor.fetchall()]

    def lookup_extension(self, search_name):
        """Fuzzy searches for an extension by its display name."""
        cursor = self.sql_conn.cursor()
        cursor.execute('''
            SELECT extension, display_name 
            FROM Directory 
            WHERE display_name LIKE ?
        ''', (f"%{search_name}%",))
        
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def get_user_timezone(self, extension):
        """Fetches the IANA timezone for a specific extension."""
        cursor = self.sql_conn.cursor()
        cursor.execute("SELECT timezone FROM Directory WHERE extension = ?", (extension,))
        result = cursor.fetchone()
        return result['timezone'] if result else 'Europe/London'

    # --- SEMANTIC MEMORY & TASKS ---
    
    def save_context_fact(self, subject, fact_text):
        doc_id = f"{subject.replace(' ', '_')}_{int(time.time())}"
        self.pref_collection.add(
            documents=[fact_text],
            metadatas=[{"subject": subject}],
            ids=[doc_id]
        )
        return True
    
    def schedule_callback(self, target_extension, scheduled_time, context):
        cursor = self.sql_conn.cursor()
        payload = json.dumps({
            "target_extension": target_extension, 
            "context": context
        })
        cursor.execute('''
            INSERT INTO Tasks (task_type, payload, scheduled_time)
            VALUES (?, ?, ?)
        ''', ('outbound_call', payload, scheduled_time))
        self.sql_conn.commit()
        return True
    
    def get_pending_calls(self, target_extension):
        """Retrieves a list of pending calls for a specific extension."""
        cursor = self.sql_conn.cursor()
        cursor.execute('''
            SELECT id, scheduled_time, payload
            FROM Tasks 
            WHERE task_type = 'outbound_call' 
            AND status = 'pending' 
            AND payload LIKE ?
        ''', (f'%"target_extension": "{target_extension}"%',))
        
        results = []
        for row in cursor.fetchall():
            payload_data = json.loads(row['payload'])
            results.append({
                "task_id": row['id'],
                "scheduled_time": row['scheduled_time'],
                "context": payload_data.get('context', 'No context')
            })
        return results

    def cancel_task_by_id(self, task_id):
        """Surgically cancels a specific task by its primary key ID."""
        cursor = self.sql_conn.cursor()
        cursor.execute('''
            UPDATE Tasks 
            SET status = 'cancelled' 
            WHERE id = ? AND status = 'pending'
        ''', (task_id,))
        self.sql_conn.commit()
        return cursor.rowcount