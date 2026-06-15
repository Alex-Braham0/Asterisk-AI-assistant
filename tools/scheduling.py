import datetime
import zoneinfo
from dateutil import parser
from tools.base import BaseTool

class ScheduleOutboundCall(BaseTool):
    name = "schedule_outbound_call"
    description = "Schedules a future outbound call. You must define priority, initiator, and exact execution instructions."
    auth_level = 10

    parameters = {
        "type": "OBJECT",
        "properties": {
            "target_extension": {
                "type": "STRING", 
                "description": "The EXACT NUMERIC SIP extension. NEVER use names."
            },
            "scheduled_time": {
                "type": "STRING", 
                "description": "The precise date and time in 'YYYY-MM-DD HH:MM:SS' format."
            },
            "priority": {
                "type": "STRING",
                "enum": ["low", "normal", "high", "emergency"],
                "description": "Emergency bypasses target DND settings."
            },
            "execution_context": {
                "type": "STRING", 
                "description": "Explicit instructions for the AI when making the call (e.g., 'Ask John if the server is patched')."
            },
            "require_follow_up": {
                "type": "BOOLEAN",
                "description": "If true, schedule another call back to the current user to report the outcome."
            }
        },
        "required": ["target_extension", "scheduled_time", "priority", "execution_context", "require_follow_up"]
    }

    async def execute(self, session, args):
        raw_target = str(args["target_extension"]).strip()
        scheduled_local_str = args["scheduled_time"]
        
        if not raw_target.isdigit():
            resolved = await session.db.endpoints.lookup_extension(raw_target)
            if resolved:
                target = resolved['extension']
            else:
                return {"status": "failed", "message": f"Could not find a numeric extension for '{raw_target}'."}
        else:
            target = raw_target
        
        try:
            user_tz_str = await session.db.endpoints.get_resolved_timezone(target)
            local_tz = zoneinfo.ZoneInfo(user_tz_str)
            
            naive_dt = parser.parse(scheduled_local_str)
            local_dt = naive_dt.replace(tzinfo=local_tz)
            utc_dt = local_dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            
            payload = {
                "context": args["execution_context"],
                "priority": args["priority"],
                "require_follow_up": args["require_follow_up"],
                "initiator": getattr(session, 'target_extension', 'Unknown')
            }
            
            await session.db.tasks.schedule_callback(target, utc_dt, payload) 
            
            return {
                "status": "success", 
                "message": f"Call successfully scheduled for {scheduled_local_str} local time."
            }
        except Exception as e:
            return {"status": "failed", "message": f"Scheduling error: {str(e)}"}

class CancelScheduledCall(BaseTool):
    name = "cancel_scheduled_call"
    description = "Cancel a specific scheduled call. You MUST use check_scheduled_calls first to get the task_id."
    auth_level = 10

    parameters = {
        "type": "OBJECT",
        "properties": {
            "task_id": {
                "type": "INTEGER", 
                "description": "The exact numerical task_id returned from check_scheduled_calls."
            }
        },
        "required": ["task_id"]
    }

    async def execute(self, session, args):
        task_id = args.get("task_id")
        
        if not isinstance(task_id, int):
            return {"status": "failed", "message": "task_id must be a valid integer."}
            
        try:
            cancelled_count = await session.db.tasks.cancel_task_by_id(task_id)
            if cancelled_count > 0:
                return {"status": "success", "message": f"Successfully cancelled task ID {task_id}."}
            else:
                return {"status": "failed", "message": f"Task ID {task_id} not found or already processed."}
        except Exception as e:
            return {"status": "failed", "message": f"Database error: {str(e)}"}