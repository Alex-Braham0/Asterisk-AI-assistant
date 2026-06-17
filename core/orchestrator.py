import asyncio
from ai.session import CallSession
from core.scheduler import BackgroundScheduler
from core.state_manager import CallStateManager
from db.connection import DatabaseConnection
from telephony.pool import ChannelPoolManager

class SIPAgentOrchestrator:
    def __init__(self, config, db: DatabaseConnection, loop: asyncio.AbstractEventLoop):
        self.config = config
        self.db = db
        self.loop = loop
        self.state_manager = CallStateManager()
        self.seen_caller_numbers = set()
        
        self.pool = ChannelPoolManager(capacity=5, config=self.config, loop=self.loop, inbound_handler=self._handle_ringing_call)
        self.scheduler = BackgroundScheduler(self.config, self.db, self.pool)

    def start(self) -> None:
        print("\n--- SIP Agent Swarm Online ---")
        print("Waiting for phone transactions and autonomous missions...")
        self.loop.create_task(self.scheduler.run())
        
        try:
            self.loop.run_forever()
        except KeyboardInterrupt:
            print("\nIntercepted shutdown directive. Safely clearing connection tracks...")
            self.pool.shutdown_all()
            self.scheduler.stop()
            self.loop.stop()

    def _handle_ringing_call(self, channel, call) -> None:
        call_id = getattr(call, '_id', 'Unknown')
        caller_number = call.request.headers.get('From', {}).get('number', 'Unknown')
        
        # THE FIX: Actively reject any inbound calls Asterisk accidentally sends to the background workers.
        # This instantly forces Asterisk to try the next line until it hits the Leader.
        if channel.channel_id != 0:
            print(f"[Swarm Routing] Headless Worker {channel.channel_id} actively rejecting inbound human call.")
            channel.drop_call()
            return
            
        if caller_number in self.seen_caller_numbers:
            channel.drop_call()
            return
            
        self.seen_caller_numbers.add(caller_number)
        
        print(f"\n[App] Inbound call incoming on Leader Channel {channel.channel_id}. Initializing line track ID: {call_id}...")
        future = asyncio.run_coroutine_threadsafe(self._process_inbound_call(channel, call, caller_number), self.loop)
        
        def check_handler_exception(f):
            try:
                f.result()
            except Exception as e:
                import traceback
                print(f"[App Error] Unhandled exception thrown inside inbound call handler routine: {e}")
                traceback.print_exc()
                
        future.add_done_callback(check_handler_exception)

    async def _process_inbound_call(self, channel, call, caller_number) -> None:
        session = CallSession(call, channel, self.config, self.db)
        call_id = getattr(call, '_id', None)
        
        if call_id:
            await self.state_manager.register_session(call_id, session)
            
        # Allow the human to hear the ringing tone while the AI connects to Google's servers.
        connected = await session.setup_connection(direction="inbound")
        
        if connected:
            try:
                await session.run_bridge()
            finally:
                if call_id:
                    await self.state_manager.unregister_session(call_id)
                if caller_number in self.seen_caller_numbers:
                    self.seen_caller_numbers.remove(caller_number)
        else:
            try:
                channel.drop_call()
            except Exception:
                pass
            if call_id:
                await self.state_manager.unregister_session(call_id)
            if caller_number in self.seen_caller_numbers:
                self.seen_caller_numbers.remove(caller_number)
            channel.is_busy = False