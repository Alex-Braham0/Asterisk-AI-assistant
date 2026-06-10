from .base import BaseTool

class SearchDirectory(BaseTool):
    name = "search_directory"
    description = "Looks up physical hardware endpoints/phones (e.g., 'Living Room', 'Front Desk'). Do NOT use this to find people. Leave search_term empty to get all hardware endpoints."
    auth_level = 10

    parameters = {
        "type": "OBJECT",
        "properties": {
            "search_term": {
                "type": "STRING", 
                "description": "The name of the physical location or endpoint. Strip possessives."
            }
        }
    }

    async def execute(self, session, args):
        search_term = args.get("search_term", "").strip()
        
        if not search_term:
            directory = await session.db_manager.get_full_directory()
            return {"status": "success", "directory": directory}
            
        clean_target = search_term.lower().replace("'s", "").replace("room", "").strip()
        result = await session.db_manager.lookup_extension(clean_target)
        
        if result:
            return {"status": "success", "data": result}
        return {"status": "failed", "message": f"No physical endpoint found matching '{clean_target}'."}


class SearchUsers(BaseTool):
    name = "search_users"
    description = "Searches for a registered human user by their spoken name or nickname. You MUST use this tool first if a caller identifies themselves on a shared phone to get their exact 'user_id' before using 'set_active_user'."
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
        
        results = await session.db_manager.resolve_users_by_name(spoken_name)
        
        if not results:
            return {
                "status": "failed", 
                "message": f"No users found matching the name or alias '{spoken_name}'. Ask the caller if they are registered under a different name."
            }
            
        if len(results) > 1:
            return {
                "status": "collision",
                "message": f"Multiple users found matching '{spoken_name}'. You MUST ask the user a non-private question to disambiguate (e.g., ask for their last name or department).",
                "matches": results
            }
            
        return {
            "status": "success",
            "message": "User definitively isolated. You should now call set_active_user with the provided user_id.",
            "user_id": results[0]['id'],
            "primary_name": results[0]['primary_name']
        }