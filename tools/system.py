import time
import asyncio
from .base import BaseTool

class SubmitCallSummary(BaseTool):
    name = "submit_call_summary"
    description = "DO NOT USE MANUALLY. Only use when explicitly commanded by the system. Provide a highly detailed summary of the call to trigger the memory processing daemon."
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
        
        # Inject the active user ID into the payload so the memory daemon knows whose profile to update
        args["user_id"] = getattr(session, 'active_user_id', None)
        
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
    description = "Binds the active call session to a specific user. Use ONLY after resolving the correct 'user_id' via the 'search_users' tool."
    auth_level = 0

    parameters = {
        "type": "OBJECT",
        "properties": {
            "user_id": {
                "type": "INTEGER", 
                "description": "The exact integer ID of the user."
            }
        },
        "required": ["user_id"]
    }

    async def execute(self, session, args):
        user_id = args.get("user_id")
        extension = getattr(session, 'target_extension', 'Unknown')
        
        # 1. Fetch baseline user identity
        query = "SELECT primary_name, current_timezone FROM Users WHERE id = $1 LIMIT 1"
        async with session.db_manager.pool.acquire() as conn:
            user_data = await conn.fetchrow(query, user_id)

        if not user_data:
            return {"status": "failed", "message": f"User ID '{user_id}' not found in database."}

        # 2. Determine security scope (Are they on a private phone or a public shared phone?)
        access_level = await session.db_manager.get_dynamic_access_level(extension, user_id)
        
        # 3. Retrieve partitioned memory based on the security scope
        user_memory = await session.db_manager.get_user_memory(user_id, access_level)
        
        # 4. Bind to session state
        session.active_user_id = user_id
        session.active_user_level = access_level
        
        # 5. Inject the context payload back into the live LLM prompt
        injection_text = (
            f"IDENTITY CONFIRMED. Active User is now {user_data['primary_name']}. "
            f"New Timezone is {user_data['current_timezone']}. "
            f"CURRENT ACCESS LEVEL: {access_level}. "
            f"USER MEMORY PROFILE:\n{user_memory}"
        )
        asyncio.create_task(session.gemini_client.send_system_event(injection_text))
        
        return {
            "status": "success", 
            "message": f"Context switched to {user_data['primary_name']}. Access level is {access_level}."
        }