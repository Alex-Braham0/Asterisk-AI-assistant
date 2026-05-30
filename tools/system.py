import time
import asyncio
from .base import BaseTool

class SubmitCallSummary(BaseTool):
    name = "submit_call_summary"
    description = "DO NOT USE MANUALLY. Only use when explicitly commanded by the system. Provide a highly detailed summary of the call."
    auth_level = 0

    parameters = {
        "type": "OBJECT",
        "properties": {
            "detailed_transcript_summary": {
                "type": "STRING", 
                "description": "Comprehensive paragraph detailing exactly what happened and decisions made."
            },
            "key_exchanges": {
                "type": "ARRAY", 
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "speaker": {"type": "STRING", "description": "Who is speaking (User or Winston)"},
                        "dialogue": {"type": "STRING", "description": "The exact quote"}
                    },
                    "required": ["speaker", "dialogue"]
                },
                "description": "Crucial exact quotes formatted as objects."
            },
            "proposed_memory_updates": {
                "type": "ARRAY", 
                "items": {"type": "STRING"},
                "description": "Specific, actionable proposals for the Memory Manager AI to update."
            },
            "action_items": {
                "type": "ARRAY", 
                "items": {"type": "STRING"}
            }
        },
        "required": ["detailed_transcript_summary", "key_exchanges", "proposed_memory_updates", "action_items"]
    }

    async def execute(self, session, args):
        extension = getattr(session, 'target_extension', 'Unknown')
        
        if session.bridge_start_time:
            args["duration_seconds"] = int(time.time() - session.bridge_start_time)
            args["start_time"] = session.bridge_start_datetime
        else:
            args["start_time"] = "Unknown"
            args["duration_seconds"] = 0
            
        args["extension"] = extension
        args["timezone"] = await session.db_manager.get_user_timezone(extension)

        print(f"[ToolRegistry] Summary received. Queuing update for extension {extension}.")
        asyncio.create_task(session.db_manager.spool_call_summary(extension, args))
        
        session.drop_call()
        session.terminate_bridge()
        return None

class SetActiveUser(BaseTool):
    name = "set_active_user"
    description = "Call this immediately when a user states their name on a shared phone. Loads their personal profile."
    auth_level = 0

    parameters = {
        "type": "OBJECT",
        "properties": {
            "user_name": {"type": "STRING", "description": "The name of the user speaking."}
        },
        "required": ["user_name"]
    }

    async def execute(self, session, args):
        user_name = args.get("user_name")
        
        query = "SELECT id, access_level, current_timezone FROM Users WHERE name ILIKE $1 LIMIT 1"
        async with session.db_manager.pool.acquire() as conn:
            user_data = await conn.fetchrow(query, user_name)

        if not user_data:
            return {"status": "failed", "message": f"User '{user_name}' not found in database."}

        session.active_user_level = user_data['access_level']
        user_memory = await session.db_manager.get_user_memory(user_name)
        
        injection_text = (
            f"IDENTITY CONFIRMED. Active User is now {user_name}. "
            f"New Timezone is {user_data['current_timezone']}. "
            f"USER MEMORY PROFILE: {user_memory}"
        )
        asyncio.create_task(session.gemini_client.send_system_event(injection_text))
        
        return {"status": "success", "message": f"Context switched to {user_name}."}