import pymysql
import asyncio

class PBXSynchronizer:
    def __init__(self, config, db_manager):
        self.config = config
        self.db_manager = db_manager

    async def run_sync(self):
        """Pulls extensions from FreePBX and upserts them into PostgreSQL."""
        print("[Sync] Initiating FreePBX Directory Sync...")
        
        freepbx_ip = self.config.get("freepbx_db_ip")
        freepbx_user = self.config.get("freepbx_db_user")
        freepbx_pass = self.config.get("freepbx_db_pass")
        
        if not all([freepbx_ip, freepbx_user, freepbx_pass]):
            print("[Sync Error] FreePBX MySQL credentials missing from config.json.")
            return False

        try:
            # Connect to FreePBX MariaDB/MySQL
            pbx_conn = pymysql.connect(
                host=freepbx_ip, user=freepbx_user, password=freepbx_pass,
                database='asterisk', cursorclass=pymysql.cursors.DictCursor
            )
            
            with pbx_conn.cursor() as cursor:
                cursor.execute("SELECT extension, name FROM users")
                pbx_extensions = cursor.fetchall()
            pbx_conn.close()

            # Ensure PostgreSQL pool is active
            if not self.db_manager.pool:
                await self.db_manager.connect()

            active_extensions = []
            
            # Upsert into PostgreSQL safely
            for ext in pbx_extensions:
                ext_num = str(ext['extension'])
                name = ext['name']
                await self.db_manager.upsert_endpoint(ext_num, name)
                active_extensions.append(ext_num)

            # Deactivate extensions that have been deleted from FreePBX
            await self.db_manager.deactivate_missing_endpoints(active_extensions)

            print(f"[Sync] Complete! Phonebook verified with {len(active_extensions)} endpoints.")
            return True

        except Exception as e:
            print(f"[Sync Error] Failed to sync with FreePBX: {e}")
            return False