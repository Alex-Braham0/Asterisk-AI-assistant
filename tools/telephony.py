import asyncio
from tools.base import BaseTool

class TransferCall(BaseTool):
    name = "transfer_call"
    description = "Transfers the current call to another extension. Use this when the user asks to speak to a human."
    auth_level = 0

    parameters = {
        "type": "object",
        "properties": {
            "target_extension": {
                "type": "string",
                "description": "The numeric extension to transfer to (e.g., '6' or '16')."
            },
            "reason": {
                "type": "string",
                "description": "The reason for the transfer."
            }
        },
        "required": ["target_extension"]
    }

    async def execute(self, session, args):
        target = args.get("target_extension")
        reason = args.get("reason", "No reason provided")
        
        try:
            session.engine.transfer_call(target)
            asyncio.create_task(session.trigger_summary(
                reason=f"Call transferred to {target}. Reason: {reason}"
            ))
            return {"status": "success", "message": f"Transferring to {target} initiated."}
        except Exception as e:
            return {"status": "failed", "message": f"Transfer failed: {e}"}

class SendDTMF(BaseTool):
    name = "send_dtmf"
    description = "Presses a key on the phone's dialpad. Useful for navigating automated menus."
    auth_level = 10

    parameters = {
        "type": "object",
        "properties": {
            "digit": {
                "type": "string",
                "description": "A single character to press: 0-9, *, or #"
            }
        },
        "required": ["digit"]
    }

    async def execute(self, session, args):
        digit = args.get("digit")
        try:
            session.engine.send_dtmf(digit)
            return {"status": "success", "message": f"Successfully pressed {digit}."}
        except Exception as e:
            return {"status": "failed", "message": f"Failed to send DTMF: {e}"}

class EndCall(BaseTool):
    name = "end_call"
    description = "Hangs up the phone. Use this ONLY when the user explicitly asks you to hang up, say goodbye, or terminate the call."
    auth_level = 0

    parameters = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Why you are hanging up."
            }
        }
    }

    async def execute(self, session, args):
        print(f"[ToolRegistry] AI initiated hangup. Reason: {args.get('reason', 'None provided')}")
        session.drop_call()
        return {"status": "success", "message": "Call physically dropped. System will now prompt for summary."}