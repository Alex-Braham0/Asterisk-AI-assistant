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
    description = "Hangs up the phone. After using this, STOP speaking and wait silently. The system will automatically send a SYSTEM COMMAND prompting you to use the 'submit_call_summary' tool. Do NOT submit the summary yourself until commanded."
    auth_level = 0
    parameters = {
        "type": "object",
        "properties": {
            "reason": {"type": "string"}
        }
    }

    async def execute(self, session, args):
        # 1. Brief pause to allow the LLM's final text tokens to reach the TTS generator
        await asyncio.sleep(0.5)
        
        # 2. Smart Drain: Actively monitor the OS-level transmit buffer
        empty_cycles = 0
        max_wait_cycles = 150 # Absolute failsafe (15 seconds) preventing infinite holds
        
        for _ in range(max_wait_cycles):
            # Safely check the bytearray length using the existing thread lock
            with session.engine.tx_lock:
                buffer_size = len(session.engine.tx_buffer)
            
            if buffer_size > 0:
                empty_cycles = 0 # AI is actively pushing audio; reset silence counter
            else:
                empty_cycles += 1 # Buffer is empty; increment silence counter
            
            # Wait for 10 consecutive empty cycles (1.0 seconds of unbroken silence)
            # This bridges the gap between network jitter and API chunking delays
            if empty_cycles >= 10:
                break
                
            await asyncio.sleep(0.1)

        # 3. Audio has definitively finished playing. Execute physical hangup.
        session.drop_call()
        
        # FORCE unblock the bridge to guarantee the summary system command fires immediately
        session.call_dropped_event.set() 
        return {
            "status": "success", 
            "internal_directive": "Call dropped successfully. Now wait silently for the system command to submit the summary."
        }

class ExecuteOutboundDial(BaseTool):
    name = "execute_outbound_dial"
    description = "Dials an external number. Use this to contact humans to fulfill your mission."
    auth_level = 10
    parameters = {
        "type": "OBJECT",
        "properties": {"target_extension": {"type": "STRING"}}
    }

    async def execute(self, session, args):
        target = args.get("target_extension")
        
        # We don't need to lease from a pool anymore. The Scheduler already locked the engine for us.
        call = session.engine.make_outbound_call(target)
        if not call:
             return {"status": "failed", "message": "Failed to dial. Line may be stuck."}
             
        try:
            await asyncio.wait_for(call.answered_event.wait(), timeout=30.0)
            
            # Hot-swap the background socket's audio onto the live phone call
            session.gemini_socket.pbx_to_ai_queue = session.engine.pbx_to_ai_queue
            session.gemini_socket.pbx_inject_callback = session.engine.inject_audio
            session.gemini_socket.pbx_flush_callback = session.engine.flush_tx_buffer
            
            await session.gemini_socket.send_system_event("Call connected. The human has picked up the phone. Speak immediately.")
            await call.ended_event.wait()
            return {"status": "success"}
        except asyncio.TimeoutError:
            session.engine.drop_call()
            return {
                "status": "failed", 
                "message": "Call timed out. The user did not answer.",
                "internal_directive": "If you have an alternative extension for this user, you MUST use this tool again to try the next number. Do not mark the mission as complete until all known devices have been tried."
            }
        finally:
            # Isolate the background agent again
            dummy_queue = asyncio.Queue()
            session.gemini_socket.pbx_to_ai_queue = dummy_queue
            session.gemini_socket.pbx_inject_callback = lambda x: None
            session.gemini_socket.pbx_flush_callback = lambda: None