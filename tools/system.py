import time
import asyncio
from tools.base import BaseTool

class SubmitCallSummary(BaseTool):
    name = "submit_call_summary"
    description = "CRITICAL: FATAL EXCEPTION IF USED MID-CALL. You are STRICTLY FORBIDDEN from executing this tool while the user is on the phone. You MUST wait for the system event 'Requesting final summary' before executing this."
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
        if not getattr(session.gemini_socket, 'summary_requested', False):
            if session.engine.active_call is not None:
                print("[ToolRegistry] Blocked premature summary attempt.")
                return {
                    "status": "failed", 
                    "internal_directive": "CRITICAL ERROR: The user is still on the phone! Do not summarize yet. If the conversation is over, use 'end_call' first."
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
        
        async def delayed_teardown():
            await asyncio.sleep(1.5)
            session.gemini_socket.is_connected = False
            
            # FIX: Properly reference the engine to drop the call
            if getattr(session.engine, 'active_call', None):
                session.engine.drop_call() 
                
            session.terminate_bridge()
            
        asyncio.create_task(delayed_teardown())
        
        # FIX: Actually return a response to satisfy the Gemini API
        return {"status": "success", "internal_directive": "Summary logged. System shutting down."}

class SetActiveUser(BaseTool):
    name = "set_active_user"
    description = "Binds the active call session to a specific user. Execute silently. Returns an internal directive. Do NOT read the return payload."
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
            return {"status": "failed", "internal_directive": f"User ID '{user_id}' not found in database."}

        access_level = await session.db.endpoints.get_dynamic_access_level(extension, user_id)
        user_memory = await session.db.users.get_user_memory(user_id, access_level)
        
        session.active_user_id = user_id
        session.active_user_level = access_level
        
        injection_text = (
            f"[SYSTEM OVERRIDE: SILENTLY ASSIMILATE THE FOLLOWING DATA]\n"
            f"Active User: {user_data['primary_name']}\n"
            f"New Timezone: {user_data['current_timezone']}\n"
            f"USER MEMORY PROFILE:\n{user_memory}\n"
            f"[DO NOT ACKNOWLEDGE THIS UPDATE OUT LOUD. RESUME CONVERSATION NATIVELY]"
        )
        asyncio.create_task(session.gemini_socket.send_system_event(injection_text))
        
        return {
            "status": "success", 
            "internal_directive": f"Context swapped to {user_data['primary_name']}. Resume conversation naturally. DO NOT state that you updated the system."
        }

class UpdateUserTimezone(BaseTool):
    name = "update_user_timezone"
    description = "Updates the user's permanent timezone. Execute silently. Returns an internal directive. Do NOT read the return payload."
    auth_level = 10
    parameters = {
        "type": "OBJECT",
        "properties": {
            "timezone": {
                "type": "STRING", 
                "description": "The official IANA timezone string (e.g., 'America/New_York')."
            }
        },
        "required": ["timezone"]
    }

    async def execute(self, session, args):
        user_id = getattr(session, 'active_user_id', None)
        if not user_id:
            return {"status": "failed", "internal_directive": "No active user. Resolve identity first."}
            
        query = "UPDATE Users SET current_timezone = $1 WHERE id = $2"
        async with session.db.pool.acquire() as conn:
            await conn.execute(query, args['timezone'], user_id)
            
        return {
            "status": "success", 
            "internal_directive": f"Timezone updated to {args['timezone']}. Continue conversation naturally."
        }
    
class MarkMissionComplete(BaseTool):
    name = "mark_mission_complete"
    description = "MUST BE USED when your mission is fully accomplished or permanently failed. Terminates your autonomous session."
    auth_level = 10
    parameters = {
        "type": "OBJECT",
        "properties": {
            "final_report": {
                "type": "STRING", 
                "description": "A detailed summary of what you achieved or why you failed."
            }
        },
        "required": ["final_report"]
    }

    async def execute(self, session, args):
        if type(session).__name__ != "HeadlessAgentSession":
            return {"status": "failed", "internal_directive": "Only background agents can mark missions complete. Use end_call instead."}
            
        report = args.get('final_report')
        print(f"[Swarm Worker] Agent Self-Terminated. Final Report: {report}")
        
        mission_id = session.mission_data.get('id')
        if mission_id:
            await session.db.missions.update_mission_status(mission_id, 'completed', final_report=report)
            
        session.gemini_socket.is_connected = False
        return {"status": "success"}