import asyncio
from tools.base import BaseTool

class TransferCall(BaseTool):
    name = "transfer_call"
    description = "Transfers the current call to another extension. Use this when the user asks to speak to a human."
    auth_level = 0
    parameters = {
        "type": "OBJECT",
        "properties": {
            "target_extension": {"type": "STRING"},
            "reason": {"type": "STRING"}
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
        "type": "OBJECT",
        "properties": {
            "digit": {"type": "STRING"}
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
    description = "Hangs up the phone. Use this when the conversation is completely finished."
    auth_level = 0
    parameters = {
        "type": "OBJECT",
        "properties": {
            "reason": {"type": "STRING"}
        }
    }

    async def execute(self, session, args):
        await asyncio.sleep(0.5)
        
        empty_cycles = 0
        max_wait_cycles = 150 
        
        for _ in range(max_wait_cycles):
            with session.engine.tx_lock:
                buffer_size = len(session.engine.tx_buffer)
            
            if buffer_size > 0:
                empty_cycles = 0 
            else:
                empty_cycles += 1 
            
            if empty_cycles >= 10:
                break
                
            await asyncio.sleep(0.1)

        # Safely execute the drop command against the engine so Headless Agents can use it
        session.engine.drop_call()
        
        if hasattr(session, 'call_dropped_event'):
            session.call_dropped_event.set() 
            
        return {
            "status": "success", 
            "internal_directive": "Call dropped successfully. Evaluate your original mission directive. If you have follow-up calls to make, use 'execute_outbound_dial' now. If your mission is fully complete, use 'mark_mission_complete' to terminate your session."
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
        
        # Force clear zombie engine state
        session.engine.drop_call()
        await asyncio.sleep(0.5) 
        
        call = session.engine.make_outbound_call(target)
        if not call:
             return {"status": "failed", "message": "Failed to dial. Line may be stuck."}
             
        # --- THE FIX: ASYNCHRONOUS MONITORING ---
        async def monitor_call():
            try:
                # 1. Wait for the human to answer
                await asyncio.wait_for(call.answered_event.wait(), timeout=30.0)
                
                # 2. Call Answered: Setup Audio Bridge
                session.gemini_socket.pbx_inject_callback = session.engine.inject_audio
                session.gemini_socket.pbx_flush_callback = session.engine.flush_tx_buffer
                session.bridge_active = True
                
                async def bridge_uplink_audio():
                    await asyncio.sleep(0.5) # SIP click deaf period
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

                # 3. Notify the AI to speak NOW
                if session.gemini_socket and session.gemini_socket.is_connected:
                    await session.gemini_socket.send_system_event(
                        "CALL CONNECTED! The human just answered the phone. Speak your greeting IMMEDIATELY."
                    )

                # 4. Wait for the human or the AI to hang up
                await call.ended_event.wait()
                
            except asyncio.TimeoutError:
                session.engine.drop_call()
                if session.gemini_socket and session.gemini_socket.is_connected:
                    await session.gemini_socket.send_system_event(
                        "Call timed out. No one answered. You must mark the mission as failed or try another number."
                    )
            finally:
                # 5. Cleanup
                session.bridge_active = False
                if session.gemini_socket and session.gemini_socket.is_connected:
                    await session.gemini_socket.send_system_event(
                        "The human has hung up the phone. The call is disconnected. Evaluate your original mission directive. If you need to make another call (like reporting back to the user), use 'execute_outbound_dial' again now. If your mission is fully complete, use 'mark_mission_complete'."
                    )
                    session.gemini_socket.pbx_inject_callback = lambda x: None

        # Spawn the monitor task and DO NOT block
        session.spawn_managed_task(monitor_call())
        
        # Temporarily mute the AI while ringing so it doesn't try to talk to the dial tone
        session.gemini_socket.pbx_inject_callback = lambda x: None
        
        # RETURN IMMEDIATELY: This unblocks the Gemini API
        return {
            "status": "dialing", 
            "message": "Dialing initiated successfully. The phone is currently ringing. You MUST wait silently. DO NOT SPEAK. A system event will notify you the exact moment the human answers."
        }