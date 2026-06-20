import datetime
from dateutil import parser
from tools.base import BaseTool

class DelegateAutonomousTask(BaseTool):
    name = "delegate_autonomous_task"
    description = (
        "Schedules a headless AI agent to execute a complex, multi-step mission in the background at a specific time. "
        "CRITICAL: If the caller asks you to call them back later, and you know they have multiple devices "
        "(e.g., a desk phone and a mobile), you MUST verbally ask them which number they want you to call before invoking this tool."
    )
    auth_level = 10

    parameters = {
        "type": "OBJECT",
        "properties": {
            "mission_directive": {
                "type": "STRING", 
                "description": "Natural language instructions for the headless agent. CRITICAL: You MUST explicitly include the target extension or phone number in this directive (e.g., 'Call Alex back at extension 1001.'). Do not leave the agent guessing."
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
                "message": f"Mission {mission_id} successfully delegated to the background swarm. It will execute at {utc_dt} UTC."
            }
        except Exception as e:
            return {"status": "failed", "message": f"Delegation error: {str(e)}"}