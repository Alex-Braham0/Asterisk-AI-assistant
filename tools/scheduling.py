import datetime
from dateutil import parser
from tools.base import BaseTool

class DelegateAutonomousTask(BaseTool):
    name = "delegate_autonomous_task"
    description = (
        "Schedules a headless AI agent to execute a background mission. "
        "CRITICAL: This tool ONLY accepts exactly two parameters. "
        "Do NOT hallucinate a 'target_extension' parameter. You must embed any phone numbers "
        "directly inside the 'mission_directive' string."
        "IMPORTANT: If the user does not explicitly request a delay, you MUST schedule the task for the CURRENT UTC time (NOW) to execute immediately. Do not arbitrarily add 30 minutes."
    )
    auth_level = 10

    parameters = {
        "type": "OBJECT",
        "properties": {
            "mission_directive": {
                "type": "STRING", 
                "description": "Natural language instructions. MUST contain the target extension (e.g., 'Call Alex back at extension 6.')."
            },
            "scheduled_time_utc": {
                "type": "STRING", 
                "description": "The precise target execution time in 'YYYY-MM-DD HH:MM:SS' UTC format."
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
                # --- FIX: Gag Order added to the return message ---
                "message": f"Mission {mission_id} successfully delegated. SYSTEM DIRECTIVE: DO NOT verbally acknowledge this success to the user. Do not say 'Understood' or 'I have scheduled it'. Simply continue your previous thought or yield your turn."
            }
        except Exception as e:
            return {"status": "failed", "message": f"Delegation error: {str(e)}"}