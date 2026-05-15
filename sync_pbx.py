import pymysql

class PBXSynchronizer:
    def __init__(self, config, db_manager):
        self.config = config
        self.db_manager = db_manager

    def run_sync(self):
        """Pulls extensions from FreePBX and perfectly mirrors them in SQLite."""
        print("[Sync] Initiating FreePBX Directory Sync...")
        
        freepbx_ip = self.config.get("freepbx_db_ip")
        freepbx_user = self.config.get("freepbx_db_user")
        freepbx_pass = self.config.get("freepbx_db_pass")
        
        if not all([freepbx_ip, freepbx_user, freepbx_pass]):
            print("[Sync Error] FreePBX MySQL credentials missing from config.json.")
            return False

        try:
            pbx_conn = pymysql.connect(
                host=freepbx_ip, user=freepbx_user, password=freepbx_pass,
                database='asterisk', cursorclass=pymysql.cursors.DictCursor
            )
            
            with pbx_conn.cursor() as cursor:
                cursor.execute("SELECT extension, name FROM users")
                pbx_extensions = cursor.fetchall()
            pbx_conn.close()

            # Update Local SQLite Directory
            sqlite_cursor = self.db_manager.sql_conn.cursor()
            
            # Wipe the slate clean for a perfect 1:1 mirror
            sqlite_cursor.execute("DELETE FROM Directory")
            
            count = 0
            for ext in pbx_extensions:
                sqlite_cursor.execute(
                    "INSERT INTO Directory (extension, display_name) VALUES (?, ?)", 
                    (ext['extension'], ext['name'])
                )
                count += 1

            self.db_manager.sql_conn.commit()
            print(f"[Sync] Complete! Phonebook updated with {count} endpoints.")
            return True

        except Exception as e:
            print(f"[Sync Error] Failed to sync with FreePBX: {e}")
            return False