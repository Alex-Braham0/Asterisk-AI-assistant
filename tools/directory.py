from .base import BaseTool

class SearchDirectory(BaseTool):
    name = "search_directory"
    description = "Looks up internal SIP extensions. Provide a name to search, or leave blank to retrieve the entire directory."
    auth_level = 10

    parameters = {
        "type": "OBJECT",
        "properties": {
            "search_term": {
                "type": "STRING", 
                "description": "The core name to search for. Strip possessives (e.g., 'Ash', not 'Ash's room'). Leave completely empty to get all users."
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
        return {"status": "failed", "message": f"No endpoint found matching '{clean_target}'."}