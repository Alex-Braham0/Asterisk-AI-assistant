import pymysql
import asyncio
import asyncpg
import re

class PBXSynchronizer:
    def __init__(self, config, pool: asyncpg.Pool):
        self.config = config
        self.pool = pool

    def _is_human_name(self, name: str) -> bool:
        """Heuristic validator to identify non-human structural/department labels."""
        clean_name = str(name).strip().lower()
        generic_keywords = [
            'desk', 'front', 'queue', 'sales', 'support', 'ext', 'room', 
            'lobby', 'conference', 'office', 'main', 'phone', 'door'
        ]
        
        # If it has numbers, it's highly likely an extension label, not a name
        if re.search(r'\d', clean_name):
            return False
            
        # Check against the blacklist of structural terms
        if any(keyword in clean_name for keyword in generic_keywords):
            return False
            
        return True

    async def run_sync(self) -> bool:
        print("[Sync] Initiating FreePBX Directory Sync with User Mapping...")
        
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
            
            for ext in pbx_extensions:
                ext_num = str(ext['extension'])
                raw_name = ext['name']
                
                is_human = self._is_human_name(raw_name)
                device_type = 'STATIC_PRIVATE' if is_human else 'STATIC_SHARED'
                
                ep_query = """
                    INSERT INTO Endpoints (extension, display_name, is_active, device_type)
                    VALUES ($1, $2, TRUE, $3)
                    ON CONFLICT (extension) 
                    DO UPDATE SET 
                        display_name = EXCLUDED.display_name, 
                        is_active = TRUE, 
                        device_type = EXCLUDED.device_type; 
                """
                
                async with self.pool.acquire() as conn:
                    # Execute all relation building within a single atomic transaction
                    async with conn.transaction():
                        await conn.execute(ep_query, ext_num, raw_name, device_type)
                        
                        # Only bootstrap the user/device mapping if it's an actual person
                        if is_human:
                            user_query = """
                                WITH new_user AS (
                                    INSERT INTO Users (primary_name)
                                    SELECT $1 WHERE NOT EXISTS (SELECT 1 FROM Users WHERE primary_name = $1)
                                    RETURNING id
                                )
                                SELECT id FROM new_user
                                UNION ALL
                                SELECT id FROM Users WHERE primary_name = $1 LIMIT 1;
                            """
                            link_query = """
                                INSERT INTO Endpoint_Users (extension, user_id, is_default, access_level)
                                VALUES ($1, $2, TRUE, 'PRIVATE')
                                ON CONFLICT (extension, user_id) 
                                DO UPDATE SET is_default = TRUE;
                            """
                            user_id = await conn.fetchval(user_query, raw_name)
                            if user_id:
                                await conn.execute(link_query, ext_num, user_id)
                            
                active_extensions.append(ext_num)

            if active_extensions:
                deactivate_query = "UPDATE Endpoints SET is_active = FALSE WHERE extension != ALL($1::varchar[])"
                async with self.pool.acquire() as conn:
                    await conn.execute(deactivate_query, active_extensions)

            print(f"[Sync] Complete! Synced {len(active_extensions)} endpoints.")
            return True

        except Exception as e:
            print(f"[Sync Error] Failed to sync with FreePBX: {e}")
            return False