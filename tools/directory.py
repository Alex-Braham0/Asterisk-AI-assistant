import json
from tools.base import BaseTool

class GetFullDirectory(BaseTool):
    name = "get_full_directory"
    description = "Retrieves a complete list of all physical hardware endpoints and their extension numbers. Use this when the user asks to hear all available connections."
    auth_level = 10
    parameters = {
        "type": "OBJECT",
        "properties": {} 
    }

    async def execute(self, session, args):
        directory = await session.db.endpoints.get_full_directory()
        return {"status": "success", "directory": directory}


class SearchDirectory(BaseTool):
    name = "search_directory"
    description = "Looks up a specific physical hardware endpoint or extension number (e.g., 'Living Room', 'Front Desk', or '16'). Use this to find the target_extension required for dialing."
    auth_level = 10
    parameters = {
        "type": "OBJECT",
        "properties": {
            "search_term": {
                "type": "STRING", 
                "description": "The name of the location, or the exact extension number."
            }
        },
        "required": ["search_term"]
    }

    async def execute(self, session, args):
        search_term = args.get("search_term", "").strip()
        clean_target = search_term.lower().replace("'s", "").replace("room", "").strip()
        
        result = await session.db.endpoints.lookup_extension(clean_target)
        
        if result:
            return {"status": "success", "data": result}
            
        # Smart fallback if the DB still misses a raw number
        if clean_target.isdigit():
            return {
                "status": "failed", 
                "message": f"Could not verify '{clean_target}' in the directory. However, if the human explicitly told you to call or transfer to extension {clean_target}, you may trust the human and proceed using {clean_target} as the target."
            }
            
        return {"status": "failed", "message": f"No physical endpoint found matching '{clean_target}'."}

class SearchUsers(BaseTool):
    name = "search_users"
    description = "Searches for a human user by name. Use this to find their user_id AND their list of registered devices/extensions. Always use this if you need to find someone's phone number to call them."
    auth_level = 0

    parameters = {
        "type": "OBJECT",
        "properties": {
            "spoken_name": {
                "type": "STRING", 
                "description": "The name the caller just gave (e.g., 'Alex', 'Bob', 'Lex')."
            }
        },
        "required": ["spoken_name"]
    }

    async def execute(self, session, args):
        spoken_name = args.get("spoken_name", "").strip()
        
        # Build a rich JSON array of devices so the AI knows exactly what it's looking at
        query = """
            SELECT u.id, u.primary_name,
                   json_agg(json_build_object(
                       'extension', e.extension,
                       'device_type', e.device_type,
                       'location', e.physical_location,
                       'is_default', eu.is_default
                   )) FILTER (WHERE e.extension IS NOT NULL) as devices_json
            FROM Users u
            LEFT JOIN Endpoint_Users eu ON u.id = eu.user_id
            LEFT JOIN Endpoints e ON eu.extension = e.extension
            WHERE u.primary_name ILIKE $1 OR $1 ILIKE ANY(u.aliases)
            GROUP BY u.id
        """
        async with session.db.pool.acquire() as conn:
            records = await conn.fetch(query, spoken_name)
            results = []
            
            # asyncpg returns json_agg as a string, so we safely parse it
            for r in records:
                row_dict = dict(r)
                devices_str = row_dict.get('devices_json')
                if devices_str:
                    row_dict['devices'] = json.loads(devices_str)
                else:
                    row_dict['devices'] = []
                del row_dict['devices_json']
                results.append(row_dict)
        
        if not results:
            return {
                "status": "failed", 
                "message": f"No users found matching '{spoken_name}'. Ask the caller if they are registered under a different name, or use register_new_user."
            }
            
        if len(results) > 1:
            return {
                "status": "collision",
                "message": f"Multiple users found matching '{spoken_name}'. You MUST ask the user a non-private question to disambiguate.",
                "matches": results
            }
            
        return {
            "status": "success",
            "message": "User definitively isolated. You should now call set_active_user with the provided user_id.",
            "user_id": results[0]['id'],
            "primary_name": results[0]['primary_name'],
            "devices": results[0]['devices'],
            "internal_directive": "If multiple devices are listed, prioritize calling the extension where 'is_default' is true. If none are default, prioritize 'MOBILE' devices. If the first call fails, you MUST try calling the next device in the list before giving up."
        }