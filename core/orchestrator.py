import asyncio
from telephony.engine import MediaEngine
from ai.gemini_socket import GeminiSocket

class SIPAgentOrchestrator:
    def __init__(self, api_key: str, system_prompt: str, loop: asyncio.AbstractEventLoop):
        self.api_key = api_key
        self.system_prompt = system_prompt
        self.loop = loop
        
        self.line_lock = asyncio.Lock()
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
            caller = call.request.headers['From']['caller']
            print(f"\n[Orchestrator] 📞 Inbound call ringing from: {caller}")
            
            # 1. Connect to Gemini BEFORE answering the phone
            gemini = GeminiSocket(
                api_key=self.api_key,
                system_prompt=self.system_prompt,
                pbx_to_ai_queue=engine.pbx_to_ai_queue,
                pbx_inject_callback=engine.inject_audio,
                pbx_flush_callback=engine.flush_tx_buffer
            )
            
            connected = await gemini.connect()
            if not connected:
                print("[Orchestrator] ❌ Failed to connect to Gemini. Dropping call.")
                engine.drop_call()
                return

            # 2. Answer the SIP leg now that AI is ready
            print("[Orchestrator] 🟢 Gemini connected. Answering SIP call...")
            success = await engine.answer_call()
            if not success:
                print("[Orchestrator] ❌ Caller hung up before answer. Aborting.")
                if gemini.ws:
                    await gemini.ws.close()
                return

            await call.answered_event.wait()
            print("[Orchestrator] 🎙️ Audio stream established.")

            while not engine.pbx_to_ai_queue.empty():
                try:
                    engine.pbx_to_ai_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # 3. Monitor the call lifecycle
            bridge_task = asyncio.create_task(
                gemini.run_audio_bridge(on_disconnect_callback=engine.drop_call)
            )
            call_ended_task = asyncio.create_task(call.ended_event.wait())

            try:
                done, pending = await asyncio.wait(
                    [bridge_task, call_ended_task],
                    return_when=asyncio.FIRST_COMPLETED
                )

                # 4. Handle Graceful Teardown vs Network Crash
                if call_ended_task in done:
                    print("[Orchestrator] 🔴 User hung up. Notifying Gemini...")
                    await gemini.send_system_text("SYSTEM EVENT: The user has hung up the phone. Conclude your thoughts.")
                    
                    # Wait dynamically for Gemini to finish processing the hangup event
                    await gemini.wait_for_turn_complete(timeout=5.0)
                else:
                    print("[Orchestrator] ⚠️ Gemini WebSocket dropped unexpectedly. Terminating call.")

            finally:
                # 5. Total system flush
                bridge_task.cancel()
                call_ended_task.cancel()
                engine.drop_call()
                engine.flush_tx_buffer()
                
                while not engine.pbx_to_ai_queue.empty():
                    try:
                        engine.pbx_to_ai_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                        
                try:
                    if gemini.ws and not gemini.ws.closed:
                        await gemini.ws.close()
                except Exception:
                    pass # Silence standard WebSocket closure exceptions
                    
                print("[Orchestrator] 🛑 Session fully cleared. Line lock released.")