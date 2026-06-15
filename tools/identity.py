from tools.base import BaseTool

class RegisterNewUser(BaseTool):
    name = "register_new_user"
    description = "Registers a brand new user in the system. Use this when speaking to someone unknown to build their memory profile."
    auth_level = 0
    parameters = {
        "type": "OBJECT",
        "properties": {
            "primary_name": {"type": "STRING", "description": "The person's full name."},
            "aliases": {
                "type": "ARRAY", 
                "items": {"type": "STRING"}, 
                "description": "Any nicknames they provided."
            }
        },
        "required": ["primary_name"]
    }

    async def execute(self, session, args):
        primary_name = args.get("primary_name")
        aliases = args.get("aliases", [])
        
        query = "INSERT INTO Users (primary_name, aliases) VALUES ($1, $2) RETURNING id"
        async with session.db.pool.acquire() as conn:
            new_id = await conn.fetchval(query, primary_name, aliases)
            
        return {
            "status": "success", 
            "user_id": new_id, 
            "message": f"User {primary_name} created. You MUST now execute set_active_user using ID {new_id}."
        }

class UpdateEndpointContext(BaseTool):
    name = "update_endpoint_context"
    description = "Updates the hardware profile and location of the phone currently being used. Use this for unknown devices."
    auth_level = 10
    parameters = {
        "type": "OBJECT",
        "properties": {
            "device_type": {
                "type": "STRING", 
                "enum": ["STATIC_SHARED", "STATIC_PRIVATE", "MOBILE"],
                "description": "Categorize the phone. Is it a shared home phone, a private office phone, or a mobile?"
            },
            "physical_location": {
                "type": "STRING",
                "description": "The physical location of the device (e.g., 'Kitchen', 'Living Room', 'Dave\\'s Mobile')."
            }
        },
        "required": ["device_type", "physical_location"]
    }

    async def execute(self, session, args):
        query = "UPDATE Endpoints SET device_type = $1, physical_location = $2 WHERE extension = $3"
        async with session.db.pool.acquire() as conn:
            await conn.execute(query, args['device_type'], args['physical_location'], session.target_extension)
            
        return {
            "status": "success",
            "message": "Hardware context updated successfully."
        }