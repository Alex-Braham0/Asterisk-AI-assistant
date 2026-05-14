import pymysql
import sqlite3

class PBXSynchronizer:
    def __init__(self, config, db_manager):
        self.config = config
        self.db_manager = db_manager

    def run_sync(self):
        """Pulls extensions from FreePBX and updates the local SQLite database."""
        print("[Sync] Initiating FreePBX Directory Sync...")
        
        freepbx_ip = self.config.get("freepbx_db_ip")
        freepbx_user = self.config.get("freepbx_db_user")
        freepbx_pass = self.config.get("freepbx_db_pass")
        
        if not all([freepbx_ip, freepbx_user, freepbx_pass]):
            print("[Sync Error] FreePBX MySQL credentials missing from config.json.")
            return False

        try:
            # 1. Connect to FreePBX MariaDB/MySQL
            pbx_conn = pymysql.connect(
                host=freepbx_ip,
                user=freepbx_user,
                password=freepbx_pass,
                database='asterisk',
                cursorclass=pymysql.cursors.DictCursor
            )
            
            with pbx_conn.cursor() as cursor:
                # The 'users' table in Asterisk holds the extension number and display name
                cursor.execute("SELECT extension, name FROM users")
                pbx_extensions = cursor.fetchall()
                
            pbx_conn.close()

            # 2. Update Local SQLite
            sqlite_cursor = self.db_manager.sql_conn.cursor()
            
            # Clear existing extensions to ensure a clean 1:1 mirror
            sqlite_cursor.execute("DELETE FROM Extensions")
            
            new_locations_added = 0
            extensions_added = 0
            
            for ext in pbx_extensions:
                sip_number = ext['extension']
                display_name = ext['name'] # E.g., "Kitchen"
                
                # Check if this Location already exists in our SQLite DB
                sqlite_cursor.execute("SELECT id FROM Locations WHERE name = ?", (display_name,))
                loc_row = sqlite_cursor.fetchone()
                
                if loc_row:
                    location_id = loc_row['id']
                else:
                    # It's a new room from FreePBX! Auto-create it.
                    sqlite_cursor.execute("INSERT INTO Locations (name) VALUES (?)", (display_name,))
                    location_id = sqlite_cursor.lastrowid
                    new_locations_added += 1
                
                # Map the extension to the location
                sqlite_cursor.execute(
                    "INSERT INTO Extensions (sip_username, location_id) VALUES (?, ?)", 
                    (sip_number, location_id)
                )
                extensions_added += 1

            self.db_manager.sql_conn.commit()
            print(f"[Sync] Complete! Synced {extensions_added} extensions. Auto-created {new_locations_added} new locations.")
            return True

        except Exception as e:
            print(f"[Sync Error] Failed to sync with FreePBX: {e}")
            return False