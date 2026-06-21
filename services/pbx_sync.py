import pymysql
import asyncpg

class PBXSynchronizer:
    def __init__(self, config, pool: asyncpg.Pool):
        self.config = config
        self.pool = pool

    async def run_sync(self) -> bool:
        print("[Sync] Initiating FreePBX Directory Sync (Endpoints Only)...")
        
        freepbx_ip = self.config.freepbx_db_ip
        freepbx_user = self.config.freepbx_db_user
        freepbx_pass = self.config.freepbx_db_pass
        
        if not all([freepbx_ip, freepbx_user, freepbx_pass]):
            print("[Sync Error] FreePBX MySQL credentials missing from configuration.")
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

            active_extensions = []
            
            ep_query = """
                INSERT INTO Endpoints (extension, display_name, is_active, device_type)
                VALUES ($1, $2, TRUE, 'STATIC_SHARED')
                ON CONFLICT (extension) 
                DO UPDATE SET 
                    display_name = EXCLUDED.display_name, 
                    is_active = TRUE;
            """
            
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    for ext in pbx_extensions:
                        ext_num = str(ext['extension'])
                        raw_name = ext['name']
                        
                        await conn.execute(ep_query, ext_num, raw_name)
                        active_extensions.append(ext_num)

            if active_extensions:
                deactivate_query = "UPDATE Endpoints SET is_active = FALSE WHERE extension != ALL($1::varchar[])"
                async with self.pool.acquire() as conn:
                    await conn.execute(deactivate_query, active_extensions)

            print(f"[Sync] Complete! Synced {len(active_extensions)} hardware endpoints.")
            return True

        except Exception as e:
            print(f"[Sync Error] Failed to sync with FreePBX: {e}")
            return False