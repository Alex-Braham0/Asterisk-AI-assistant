import asyncio
from datetime import datetime
from telephony.engine import MediaEngine
from ai.gemini_socket import GeminiSocket
from core.state import StateManager
from core.prompt import PromptManager
import traceback

class SIPAgentOrchestrator:
    def __init__(self, api_key: str, system_prompt: str, loop: asyncio.AbstractEventLoop):
        self.api_key = api_key
        self.system_prompt = system_prompt
        self.loop = loop
        
        self.line_lock = asyncio.Lock()
        
        # Initialize the decoupled IPC/Database manager
        self.state_mgr = StateManager()
        self.prompt_mgr = PromptManager()
        
        self.engine = MediaEngine(config=None, loop=self.loop, on_inbound_callback=self._handle_inbound_call)

    def start(self) -> None:
        self.engine.start()

    def stop(self) -> None:
        self.engine.stop()

    def _handle_inbound_call(self, engine, call) -> None:
        if self.line_lock.locked():
            print("[Orchestrator] Call rejected: Line is currently busy.")
            engine.drop_call()
            return
            
        asyncio.run_coroutine_threadsafe(self._process_inbound_call(engine, call), self.loop)

    async def _process_inbound_call(self, engine, call) -> None:
        async with self.line_lock:
            start_time = datetime.utcnow()
            caller_name = "Unknown"
            caller_number = "Unknown"
            
            try:
                # 1. Parse both Name and Extension Number from SIP headers
                from_header = call.request.headers.get('From', {})
                if isinstance(from_header, dict):
                    caller_name = str(from_header.get('caller', 'Unknown'))
                    caller_number = str(from_header.get('number', 'Unknown')).strip()

                print(f"\n[Orchestrator] 📞 Inbound call ringing from: {caller_name} (Ext: {caller_number})")
                
                # 2. Build live context dictionary
                call_context = {
                    "call_id": getattr(call, "_id", f"inc_{int(start_time.timestamp())}"),
                    "caller_name": caller_name,
                    "caller_number": caller_number,
                    "direction": "inbound",
                    "start_time": start_time.isoformat()
                }

                # 3. Dynamically compile system prompt
                dynamic_prompt = self.prompt_mgr.build_prompt(call_context)

                # Update UI state
                try:
                    await self.state_mgr.set_live_state(is_active=True, caller_id=f"{caller_name} ({caller_number})")
                except Exception as e:
                    print(f"[Orchestrator] ⚠️ UI STATE ERROR (Ignored): {e}")

                # 4. Instantiate GeminiSocket with the First Audio Event
                first_audio_event = asyncio.Event()
                def _on_audio():
                    self.loop.call_soon_threadsafe(first_audio_event.set)

                async def _safe_transfer(target):
                    print(f"[Orchestrator] ⏱️ Transfer to {target} queued. Waiting for AI to finish speaking...")
                    while True:
                        with engine.tx_lock:
                            buf_len = len(engine.tx_buffer)
                        if buf_len == 0:
                            break
                        await asyncio.sleep(0.1)
                    
                    print(f"[Orchestrator] ⏱️ Speech finished. Applying 400ms echo tail hold...")
                    await asyncio.sleep(0.4)
                    print(f"[Orchestrator] 🔀 Executing PBX transfer now.")
                    engine.transfer_call(target)

                async def _safe_hangup():
                    print(f"[Orchestrator] ⏱️ Hangup queued. Waiting for AI to finish speaking...")
                    while True:
                        with engine.tx_lock:
                            buf_len = len(engine.tx_buffer)
                        if buf_len == 0:
                            break
                        await asyncio.sleep(0.1)
                        
                    print(f"[Orchestrator] ⏱️ Speech finished. Applying 400ms echo tail hold...")
                    await asyncio.sleep(0.4)
                    print(f"[Orchestrator] 📞 Executing PBX hangup now.")
                    engine.drop_call()

                def _set_turn_lock(locked: bool):
                    if engine.active_call:
                        engine.active_call.turn_locked = locked

                gemini = GeminiSocket(
                    api_key=self.api_key,
                    system_prompt=dynamic_prompt,
                    pbx_to_ai_queue=engine.pbx_to_ai_queue,
                    pbx_inject_callback=engine.inject_audio,
                    pbx_flush_callback=engine.flush_tx_buffer,
                    call_context=call_context,
                    pbx_hangup_callback=_safe_hangup,
                    pbx_transfer_callback=_safe_transfer,
                    on_first_audio_callback=_on_audio,  # Answer-on-Audio trigger
                    pbx_ungate_mic_callback=engine.ungate_mic,
                    pbx_hold_callback=engine.hold_call
                )
                
                connected = await gemini.connect()
                if not connected:
                    print("[Orchestrator] ❌ Failed to connect to Gemini. Dropping call.")
                    engine.drop_call()
                    try:
                        await self.state_mgr.set_live_state(is_active=False)
                    except Exception:
                        pass
                    return

                # --- THE FIX: START THE BRIDGE IMMEDIATELY ---
                # The uplink/downlink loops must be running so the WebSocket can listen for audio!
                bridge_task = asyncio.create_task(
                    gemini.run_audio_bridge(on_disconnect_callback=engine.drop_call)
                )
                # ---------------------------------------------

                # 5. Ask Gemini for the greeting
                greeting_prompt = (
                    f"SYSTEM EVENT: The SIP line is connecting. The caller is {caller_name} on extension {caller_number}. "
                    f"Greet them warmly by name right now and ask how you can help."
                )
                print("[Orchestrator] 🟢 Connected to Gemini. Requesting greeting...")
                await gemini.send_system_text(greeting_prompt)

                # 6. Wait for Gemini to generate the audio payload
                try:
                    await asyncio.wait_for(first_audio_event.wait(), timeout=5.0)
                    print("[Orchestrator] 🎵 Audio received from AI.")
                except asyncio.TimeoutError:
                    print("[Orchestrator] ⚠️ Timeout waiting for AI audio. Answering anyway.")

                # 7. Answer the SIP call exactly as the audio arrives
                print("[Orchestrator] 📞 Answering SIP call...")
                success = await engine.answer_call()
                if not success:
                    print("[Orchestrator] ❌ Caller hung up while ringing. Aborting.")
                    bridge_task.cancel()
                    if gemini.ws:
                        await gemini.ws.close()
                    try:
                        await self.state_mgr.set_live_state(is_active=False)
                    except Exception:
                        pass
                    return

                await call.answered_event.wait()
                print("[Orchestrator] 🎙️ Audio stream established. Ambient audio fading up.")

                # 8. Monitor call lifecycle (bridge_task is already running now)
                call_ended_task = asyncio.create_task(call.ended_event.wait())

                done, pending = await asyncio.wait(
                    [bridge_task, call_ended_task],
                    return_when=asyncio.FIRST_COMPLETED
                )

                if call_ended_task in done:
                    if not call_context.get("summary_completed"):
                        print("[Orchestrator] 🔴 User hung up. Unlocking restricted tools and commanding AI summary...")
                        call_context["restricted_unlocked"] = True
                        
                        # --- NEW: Stop sending mic audio so it doesn't collide with the text prompt ---
                        gemini.audio_uplink_active = False
                        # ------------------------------------------------------------------------------
                        
                        await gemini.send_system_text(
                            "SYSTEM EVENT: The caller has hung up the phone. The call is over. "
                            "You MUST immediately use the `call_summary` tool to log the full transcript and summary. "
                            "Do NOT generate an audio response, just execute the tool."
                        )
                        await gemini.wait_for_turn_complete(timeout=8.0)
                    else:
                        print("[Orchestrator] 🔴 Call ended. Summary was already proactively saved by AI.")
                else:
                    print("[Orchestrator] ⚠️ Gemini WebSocket dropped unexpectedly. Terminating call.")
                    
            except Exception as e:
                print("\n[Orchestrator] 💥 CRITICAL ERROR in call loop:")
                import traceback
                traceback.print_exc()
                engine.drop_call()

            finally:
                end_time = datetime.utcnow()
                try:
                    bridge_task.cancel()
                    call_ended_task.cancel()
                except:
                    pass
                    
                engine.drop_call()
                engine.flush_tx_buffer()
                
                while not engine.pbx_to_ai_queue.empty():
                    try:
                        engine.pbx_to_ai_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                        
                try:
                    if 'gemini' in locals() and gemini.ws and not gemini.ws.closed:
                        await gemini.ws.close()
                except Exception:
                    pass
                    
                print("[Orchestrator] 🛑 Session fully cleared. Line lock released.")

                try:
                    await self.state_mgr.set_live_state(is_active=False)
                except Exception as e:
                    print(f"[Orchestrator] ⚠️ UI STATE ERROR (Ignored): {e}")

                asyncio.create_task(
                    asyncio.to_thread(
                        self.state_mgr.save_call_history, 
                        direction="inbound",
                        remote_identity=f"{caller_name} ({caller_number})",
                        start_time=start_time, 
                        end_time=end_time, 
                        status="completed"
                    )
                )