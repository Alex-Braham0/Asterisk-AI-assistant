import datetime
from dateutil import parser
from tools.base import BaseTool

class DelegateAutonomousTask(BaseTool):
    name = "delegate_autonomous_task"
    description = (
        "Schedules a headless AI agent to execute a background mission. "
        "CRITICAL: Do NOT hallucinate a 'target_extension' parameter. Embed phone numbers "
        "directly inside the 'mission_directive' string. "
        "IMPORTANT: Unless the user explicitly asks for a delay (e.g., 'in 2 hours'), you MUST schedule "
        "the task for the CURRENT time (NOW) so it executes immediately. Do not arbitrarily add time delays."
    )
    auth_level = 10

    parameters = {
        "type": "OBJECT",
        "properties": {
            "mission_directive": {
                "type": "STRING", 
                "description": "Natural language instructions. MUST contain the target extension."
            },
            "scheduled_time_utc": {
                "type": "STRING", 
                "description": "Target execution time in 'YYYY-MM-DD HH:MM:SS' UTC format. Use current UTC time for immediate execution."
            }
        },
        "required": ["mission_directive", "scheduled_time_utc"]
    }

    async def execute(self, session, args):
        user_id = getattr(session, 'active_user_id', None)
        if not user_id:
            return {"status": "failed", "message": "You must identify and set the active user before delegating tasks on their behalf."}

        try:
            utc_dt = parser.parse(args["scheduled_time_utc"]).replace(tzinfo=None)
            mission_id = await session.db.missions.create_mission(
                owner_user_id=user_id,
                run_at_utc=utc_dt,
                directive=args["mission_directive"]
            )
            return {
                "status": "success", 
                "message": f"Mission successfully delegated. SYSTEM DIRECTIVE: Continue your previous thought or yield your turn. Do not verbally confirm the technical scheduling details."
            }
        except Exception as e:
            return {"status": "failed", "message": f"Delegation error: {str(e)}"}