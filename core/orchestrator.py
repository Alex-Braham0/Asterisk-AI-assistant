import asyncio
from telephony.engine import MediaEngine
from ai.session import CallSession
from core.scheduler import BackgroundScheduler
from core.state_manager import CallStateManager
from db.connection import DatabaseConnection

class SIPAgentOrchestrator:
    def __init__(self, config, db: DatabaseConnection, loop: asyncio.AbstractEventLoop):
        self.config = config
        self.db = db
        self.loop = loop
        self.state_manager = CallStateManager()
        self.engine = MediaEngine(
            self.config.sip_ip, 
            self.config.sip_port, 
            self.config.username, 
            self.config.password,
            on_call_callback=self._handle_ringing_call,
            loop=self.loop  # Pass loop reference explicitly
        )
        self.scheduler = BackgroundScheduler(self.db, self._initiate_outbound_call)

    def start(self) -> None:
        self.engine.start()
        print("\n--- SIP Agent Online ---")
        print("Waiting for phone transactions...")
        self.loop.create_task(self.scheduler.run())
        
        try:
            self.loop.run_forever()
        except KeyboardInterrupt:
            print("\nIntercepted shutdown directive. Safely clearing connection tracks...")
            self.engine.stop()
            self.scheduler.stop()
            self.loop.stop()

    def _handle_ringing_call(self, call) -> None:
        caller_id = getattr(call, '_id', 'Unknown')
        print(f"\n[App] Inbound call connection incoming. Initializing line track ID: {caller_id}...")
        future = asyncio.run_coroutine_threadsafe(self._process_inbound_call(call), self.loop)
        
        def check_handler_exception(f):
            try:
                f.result()
            except Exception as e:
                import traceback
                print(f"[App Error] Unhandled exception thrown inside inbound call handler routine: {e}")
                traceback.print_exc()
                
        future.add_done_callback(check_handler_exception)

    async def _process_inbound_call(self, call) -> None:
        session = CallSession(call, self.engine, self.config, self.db)
        call_id = getattr(call, '_id', None)
        
        if call_id:
            await self.state_manager.register_session(call_id, session)
            
        connected = await session.setup_connection(direction="inbound")
        
        if connected:
            try:
                await session.run_bridge()
            finally:
                if call_id:
                    await self.state_manager.unregister_session(call_id)
        else:
            try:
                call.deny()
            except Exception:
                pass
            if call_id:
                await self.state_manager.unregister_session(call_id)

    async def _initiate_outbound_call(self, target_extension: str, context: str) -> bool:
        print(f"[Orchestrator] Building pre-warm context parameters for outbound route target: {target_extension}...")
        target_info = {"extension": target_extension, "context": context}
        
        session = CallSession(call=None, engine=self.engine, config=self.config, db=self.db)
        connected = await session.setup_connection(direction="outbound", target_info=target_info)
        
        if not connected:
            print("[Orchestrator] AI initialization failed. Halting outbound call sequence pipeline.")
            return False

        print(f"[Orchestrator] Core socket handshaking confirmed. Dispatching invite invite over line: {target_extension}...")
        call = self.engine.make_outbound_call(target_extension)
        
        if not call:
            print("[Orchestrator] Channel rejected. Local infrastructure state reported busy or dropped.")
            session.cleanup()
            return False
            
        session.call = call
        call_id = getattr(call, '_id', None)
        if call_id:
            await self.state_manager.register_session(call_id, session)

        timeout_seconds = 45.0
        try:
            answer_task = asyncio.create_task(call.answered_event.wait())
            end_task = asyncio.create_task(call.ended_event.wait())
            
            done, pending = await asyncio.wait(
                [answer_task, end_task],
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED
            )
            
            for task in pending:
                task.cancel()

            if not done:
                print("[Orchestrator] Line ringing exceeded maximum dial safety bounds without answering party.")
                session.cleanup()
                if call_id:
                    await self.state_manager.unregister_session(call_id)
                return False

            if call.answered_event.is_set():
                print("[Orchestrator] Remote pick-up event caught. Engaging live transaction loop bridges...")
                await session.run_bridge()
                if call_id:
                    await self.state_manager.unregister_session(call_id)
                return True
            else:
                print("[Orchestrator] Line connection cleared by network layer before session could bridge.")
                session.cleanup()
                if call_id:
                    await self.state_manager.unregister_session(call_id)
                return False

        except Exception as e:
            print(f"[Orchestrator Critical Exception Error] Session tracker lifecycle crash: {e}")
            session.cleanup()
            if call_id:
                await self.state_manager.unregister_session(call_id)
            return False