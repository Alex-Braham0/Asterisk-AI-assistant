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
        
        # --- THE FIX: FORCE CLEAR ZOMBIE ENGINE STATE ---
        # If a previous live call failed to cleanly reset the engine's internal state, 
        # it will instantly block new outbound calls. Because the Swarm Worker holds the 
        # orchestrator's line_lock, we are guaranteed the line is physically free. 
        # We forcefully drop any phantom states to unblock the engine.
        session.engine.drop_call()
        await asyncio.sleep(0.5) # Give the OS/Baresip time to clear the socket
        
        call = session.engine.make_outbound_call(target)
        if not call:
             return {"status": "failed", "message": "Failed to dial. Line may be stuck."}
             
        try:
            # --- PHASE 1: PRE-GENERATION CACHING ---
            pregen_buffer = bytearray()
            
            def capture_audio(pcm_data):
                pregen_buffer.extend(pcm_data)
            
            original_inject = session.gemini_socket.pbx_inject_callback
            session.gemini_socket.pbx_inject_callback = capture_audio
            
            asyncio.create_task(
                session.gemini_socket.send_system_event(
                    "The phone is currently ringing. Generate your greeting NOW and speak it. "
                    "Do not wait for the human to answer. Act as if they just picked up."
                )
            )
            
            await asyncio.wait_for(call.answered_event.wait(), timeout=30.0)
            
            # --- PHASE 2: CONNECTION ESTABLISHED ---
            session.gemini_socket.pbx_inject_callback = session.engine.inject_audio
            session.gemini_socket.pbx_flush_callback = session.engine.flush_tx_buffer
            
            if pregen_buffer:
                session.engine.inject_audio(bytes(pregen_buffer))
            
            session.bridge_active = True
            
            async def bridge_uplink_audio():
                await asyncio.sleep(0.5) 
                while not session.engine.pbx_to_ai_queue.empty():
                    try:
                        session.engine.pbx_to_ai_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    
                while session.bridge_active:
                    try:
                        pcm = await asyncio.wait_for(session.engine.pbx_to_ai_queue.get(), timeout=1.0)
                        await session.gemini_socket.pbx_to_ai_queue.put(pcm)
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        break

            asyncio.create_task(bridge_uplink_audio())
            
            async def monitor_call_drop():
                await call.ended_event.wait()
                session.bridge_active = False 
                if session.gemini_socket and session.gemini_socket.is_connected:
                    await session.gemini_socket.send_system_event(
                        "The human has hung up the phone. The call is disconnected. "
                        "You must now use the mark_mission_complete tool to report the outcome."
                    )
                    session.gemini_socket.pbx_inject_callback = lambda x: None

            asyncio.create_task(monitor_call_drop())
            
            return {
                "status": "success", 
                "message": "Call connected and human is on the line. Your pre-generated greeting was delivered successfully."
            }
            
        except asyncio.TimeoutError:
            session.engine.drop_call()
            session.gemini_socket.pbx_inject_callback = original_inject 
            return {
                "status": "failed", 
                "message": "Call timed out. The user did not answer.",
                "internal_directive": "If you have an alternative extension, you MUST use this tool again to try the next number."
            }