import time
import asyncio
from tools.base import BaseTool

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
        # SECURITY LOCK: Reject manual execution
        if not getattr(session.gemini_socket, 'summary_requested', False):
            print("[ToolRegistry] Blocked premature summary attempt.")
            return {
                "status": "failed", 
                "message": "CRITICAL ERROR: The call is still active! Do NOT use submit_call_summary. If the user asked you to hang up, you MUST use the `end_call` tool instead."
            }

        extension = getattr(session, 'target_extension', 'Unknown')
        args["user_id"] = getattr(session, 'active_user_id', None)
        
        if session.bridge_start_time:
            args["duration_seconds"] = int(time.time() - session.bridge_start_time)
            args["start_time"] = session.bridge_start_datetime
        else:
            args["start_time"] = "Unknown"
            args["duration_seconds"] = 0
            
        args["extension"] = extension
        args["timezone"] = await session.db.endpoints.get_resolved_timezone(extension)

        print(f"[ToolRegistry] Summary received. Queuing update for extension {extension}.")
        asyncio.create_task(session.db.tasks.spool_call_summary(extension, args))
        
        session.drop_call()
        session.terminate_bridge()
        return None

class SetActiveUser(BaseTool):
    name = "set_active_user"
    description = "Binds the active call session to a specific user. Use ONLY after resolving the correct 'user_id' via search_users or register_new_user."
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
        
        query = "SELECT primary_name, current_timezone FROM Users WHERE id = $1 LIMIT 1"
        async with session.db.pool.acquire() as conn:
            user_data = await conn.fetchrow(query, user_id)

        if not user_data:
            return {"status": "failed", "message": f"User ID '{user_id}' not found in database."}

        access_level = await session.db.endpoints.get_dynamic_access_level(extension, user_id)
        user_memory = await session.db.users.get_user_memory(user_id, access_level)
        
        session.active_user_id = user_id
        session.active_user_level = access_level
        
        injection_text = (
            f"IDENTITY CONFIRMED. Active User is now {user_data['primary_name']}. "
            f"New Timezone is {user_data['current_timezone']}. "
            f"CURRENT ACCESS LEVEL: {access_level}. "
            f"USER MEMORY PROFILE:\n{user_memory}"
        )
        asyncio.create_task(session.gemini_socket.send_system_event(injection_text))
        
        return {
            "status": "success", 
            "message": f"Context switched to {user_data['primary_name']}. Access level is {access_level}."
        }

class UpdateUserTimezone(BaseTool):
    name = "update_user_timezone"
    description = "Updates the user's permanent timezone if they explicitly say they are traveling or moving."
    auth_level = 10
    parameters = {
        "type": "OBJECT",
        "properties": {
            "timezone": {
                "type": "STRING", 
                "description": "The official IANA timezone string (e.g., 'America/New_York', 'Europe/London')."
            }
        },
        "required": ["timezone"]
    }

    async def execute(self, session, args):
        user_id = getattr(session, 'active_user_id', None)
        if not user_id:
            return {"status": "failed", "message": "No active user. Use set_active_user first."}
            
        query = "UPDATE Users SET current_timezone = $1 WHERE id = $2"
        async with session.db.pool.acquire() as conn:
            await conn.execute(query, args['timezone'], user_id)
            
        return {"status": "success", "message": f"Timezone updated to {args['timezone']}."}