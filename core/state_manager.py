import asyncio

class CallStateManager:
    """
    Critique 4 Fix: Manages registration contexts mapped by dynamic call IDs
    to allow multi-channel synchronization, breaking single-line tracking limits.
    """
    def __init__(self):
        self._active_sessions = {}
        self._lock = asyncio.Lock()

    async def register_session(self, call_id: str, session) -> None:
        async with self._lock:
            self._active_sessions[call_id] = session

    async def unregister_session(self, call_id: str) -> None:
        async with self._lock:
            if call_id in self._active_sessions:
                del self._active_sessions[call_id]

    async def get_session(self, call_id: str):
        async with self._lock:
            return self._active_sessions.get(call_id)

    async def get_all_sessions(self) -> list:
        async with self._lock:
            return list(self._active_sessions.values())