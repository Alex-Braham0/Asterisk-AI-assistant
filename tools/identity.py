import asyncio
from tools.base import BaseTool

class ResolveAndSwitchUser(BaseTool):
    name = "resolve_and_switch_user"
    description = "Consolidated tool to find or create a user by their spoken name, and immediately switch the active call context to them. Use this when the caller introduces themselves. Execute silently. Returns an internal directive. Do NOT read the return payload."
    auth_level = 0
    parameters = {
        "type": "OBJECT",
        "properties": {
            "spoken_name": {"type": "STRING", "description": "The exact name the caller provided."}
        },
        "required": ["spoken_name"]
    }

    async def execute(self, session, args):
        spoken_name = args.get("spoken_name", "").strip()
        extension = getattr(session, 'target_extension', 'Unknown')
        
        # 1. Search for user
        results = await session.db.users.resolve_users_by_name(spoken_name)
        
        user_id = None
        primary_name = spoken_name
        current_timezone = "Europe/London"

        if not results:
            # 2. Automatically create new user if not found
            query = "INSERT INTO Users (primary_name) VALUES ($1) RETURNING id"
            async with session.db.pool.acquire() as conn:
                user_id = await conn.fetchval(query, spoken_name)
        elif len(results) > 1:
            # 3. Handle collision gracefully
            return {
                "status": "collision",
                "internal_directive": f"Multiple users found matching '{spoken_name}'. Ask the caller a subtle identifying question (like their department or last name) to disambiguate."
            }
        else:
            # 4. Exact match found
            user_id = results[0]['id']
            primary_name = results[0]['primary_name']
            
        # 5. Native SetActiveUser Logic
        query_tz = "SELECT current_timezone FROM Users WHERE id = $1 LIMIT 1"
        async with session.db.pool.acquire() as conn:
            tz_val = await conn.fetchval(query_tz, user_id)
            if tz_val: current_timezone = tz_val

        access_level = await session.db.endpoints.get_dynamic_access_level(extension, user_id)
        user_memory = await session.db.users.get_user_memory(user_id, access_level)
        
        session.active_user_id = user_id
        session.active_user_level = access_level
        
        injection_text = (
            f"[SYSTEM OVERRIDE: SILENTLY ASSIMILATE THE FOLLOWING DATA]\n"
            f"Active User: {primary_name}\n"
            f"New Timezone: {current_timezone}\n"
            f"USER MEMORY PROFILE:\n{user_memory}\n"
            f"[DO NOT ACKNOWLEDGE THIS UPDATE OUT LOUD. RESUME CONVERSATION NATIVELY]"
        )
        asyncio.create_task(session.gemini_socket.send_system_event(injection_text))
        
        return {
            "status": "success", 
            "internal_directive": f"Context swapped to {primary_name}. Resume conversation naturally. DO NOT state that you updated the system."
        }

class RegisterNewUser(BaseTool):
    name = "register_new_user"
    description = "Registers a brand new user manually. Execute silently. Returns an internal directive. Do NOT read the return payload."
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
            "internal_directive": f"User {primary_name} created. You MUST now execute set_active_user using ID {new_id}. Do not say this out loud."
        }

class UpdateEndpointContext(BaseTool):
    name = "update_endpoint_context"
    description = "Updates the hardware profile and location of the phone currently being used. Execute silently. Returns an internal directive. Do NOT read the return payload."
    auth_level = 10
    parameters = {
        "type": "OBJECT",
        "properties": {
            "device_type": {
                "type": "STRING", 
                "enum": ["STATIC_SHARED", "STATIC_PRIVATE", "MOBILE"],
                "description": "Categorize the phone."
            },
            "physical_location": {
                "type": "STRING",
                "description": "The physical location of the device (e.g., 'Kitchen', 'Living Room')."
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
            "internal_directive": "Hardware context updated successfully. Continue the conversation naturally."
        }