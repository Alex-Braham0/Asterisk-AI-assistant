import sqlite3
import chromadb
import time
import json

class DatabaseManager:
    def __init__(self, sqlite_path="pbx_core.db", chroma_path="./chroma_data"):
        # 1. Initialize SQLite (Deterministic Core)
        self.sql_conn = sqlite3.connect(sqlite_path, check_same_thread=False)
        self.sql_conn.row_factory = sqlite3.Row
        self._init_sql_tables()

        # 2. Initialize ChromaDB (Semantic Brain)
        self.chroma_client = chromadb.PersistentClient(path=chroma_path)
        self.pref_collection = self.chroma_client.get_or_create_collection(name="home_context")

    def _init_sql_tables(self):
        """Creates the Location-Centric relational schema."""
        cursor = self.sql_conn.cursor()
        
        # Table 1: Physical Locations (Rooms)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                is_communal BOOLEAN DEFAULT 0
            )
        ''')
        
        # Table 2: Telephony Devices (Tied strictly to a Location)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Extensions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sip_username TEXT NOT NULL UNIQUE,
                location_id INTEGER NOT NULL,
                FOREIGN KEY(location_id) REFERENCES Locations(id)
            )
        ''')
        
        # Table 3: People (Tied to a primary location, e.g., their bedroom)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                primary_location_id INTEGER,
                FOREIGN KEY(primary_location_id) REFERENCES Locations(id)
            )
        ''')

        # The Unified Task Queue
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL, -- e.g., 'outbound_call', 'process_summary'
                payload TEXT NOT NULL,   -- JSON string containing task arguments
                scheduled_time DATETIME NOT NULL,
                status TEXT DEFAULT 'pending' -- 'pending', 'processing', 'completed', 'failed'
            )
        ''')
        self.sql_conn.commit()

    # --- ROUTING LOGIC ---

    def get_extension_for_room(self, room_name):
        """Resolves a room name (e.g., 'Kitchen') to its physical SIP extension."""
        cursor = self.sql_conn.cursor()
        cursor.execute('''
            SELECT e.sip_username 
            FROM Locations l
            JOIN Extensions e ON l.id = e.location_id
            WHERE l.name LIKE ?
        ''', (f"%{room_name}%",))
        
        result = cursor.fetchone()
        return result['sip_username'] if result else None

    def get_extension_for_person(self, person_name):
        """Resolves a person's name to the SIP extension of their primary room."""
        cursor = self.sql_conn.cursor()
        cursor.execute('''
            SELECT e.sip_username 
            FROM Users u
            JOIN Locations l ON u.primary_location_id = l.id
            JOIN Extensions e ON l.id = e.location_id
            WHERE u.name LIKE ?
        ''', (f"%{person_name}%",))
        
        result = cursor.fetchone()
        return result['sip_username'] if result else None

    # --- SEMANTIC MEMORY (ChromaDB) ---
    
    def save_context_fact(self, subject, fact_text):
        """Embeds a fact about a user or room into the vector database."""
        doc_id = f"{subject.replace(' ', '_')}_{int(time.time())}"
        self.pref_collection.add(
            documents=[fact_text],
            metadatas=[{"subject": subject}],
            ids=[doc_id]
        )
        return True
    
    def schedule_callback(self, target_extension, scheduled_time, context):
        """Inserts a pending outbound call request into the Unified Tasks queue."""
        cursor = self.sql_conn.cursor()
        
        # Package the arguments into the JSON payload expected by app.py's worker
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