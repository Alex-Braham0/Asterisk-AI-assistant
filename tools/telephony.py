import asyncio
from tools.base import BaseTool

class TransferCall(BaseTool):
    name = "transfer_call"
    description = "Transfers the current call to another extension. Use this when the user asks to speak to a human."
    auth_level = 0
    parameters = {
        "type": "object",
        "properties": {
            "target_extension": {"type": "string"},
            "reason": {"type": "string"}
        },
        "required": ["target_extension"]
    }

    async def execute(self, session, args):
        target = args.get("target_extension")
        reason = args.get("reason", "No reason provided")
        try:
            session.channel.transfer_call(target)
            asyncio.create_task(session.trigger_summary(reason=f"Call transferred to {target}. Reason: {reason}"))
            return {"status": "success", "message": f"Transferring to {target} initiated."}
        except Exception as e:
            return {"status": "failed", "message": f"Transfer failed: {e}"}

class SendDTMF(BaseTool):
    name = "send_dtmf"
    description = "Presses a key on the phone's dialpad."
    auth_level = 10
    parameters = {
        "type": "object",
        "properties": {
            "digit": {"type": "string"}
        },
        "required": ["digit"]
    }

    async def execute(self, session, args):
        digit = args.get("digit")
        try:
            session.channel.send_dtmf(digit)
            return {"status": "success", "message": f"Successfully pressed {digit}."}
        except Exception as e:
            return {"status": "failed", "message": f"Failed to send DTMF: {e}"}

class EndCall(BaseTool):
    name = "end_call"
    description = "Hangs up the phone."
    auth_level = 0
    parameters = {
        "type": "object",
        "properties": {
            "reason": {"type": "string"}
        }
    }

    async def execute(self, session, args):
        session.drop_call()
        return {"status": "success", "message": "Call dropped."}

class ExecuteOutboundDial(BaseTool):
    name = "execute_outbound_dial"
    description = "Dials an external number. Use this to contact humans to fulfill your mission. You will be connected to the audio channel upon answer."
    auth_level = 10
    parameters = {
        "type": "OBJECT",
        "properties": {
            "target_extension": {
                "type": "STRING", 
                "description": "The exact numeric SIP extension to call."
            }
        },
        "required": ["target_extension"]
    }

    async def execute(self, session, args):
        target = args.get("target_extension")
        
        channel = await session.orchestrator_pool.lease_idle_channel()
        if not channel:
            return {"status": "failed", "message": "All telephony channels are currently in use. Try again later."}
            
        try:
            call = channel.make_outbound_call(target)
            if not call:
                 return {"status": "failed", "message": "Failed to spawn call."}
                 
            await asyncio.wait_for(call.answered_event.wait(), timeout=30.0)
            
            session.gemini_socket.pbx_to_ai_queue = channel.pbx_to_ai_queue
            session.gemini_socket.pbx_inject_callback = channel.inject_audio
            session.gemini_socket.pbx_flush_callback = channel.flush_tx_buffer
            
            await session.gemini_socket.send_system_event("Call connected. The human has picked up the phone. Speak immediately.")
            
            await call.ended_event.wait()
            return {"status": "success", "message": f"Call with {target} completed."}
        except asyncio.TimeoutError:
            channel.drop_call()
            return {"status": "failed", "message": "Call timed out."}
        finally:
            channel.is_busy = False
            
            dummy_queue = asyncio.Queue()
            session.gemini_socket.pbx_to_ai_queue = dummy_queue
            session.gemini_socket.pbx_inject_callback = lambda x: None
            session.gemini_socket.pbx_flush_callback = lambda: None