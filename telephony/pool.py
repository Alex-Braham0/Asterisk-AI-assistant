import asyncio
from telephony.audio_router import PulseAudioRouter
from telephony.media_channel import MediaChannel

class ChannelPoolManager:
    def __init__(self, capacity: int, config, loop, inbound_handler):
        self.capacity = capacity
        self.config = config
        self.loop = loop
        self.channels: list[MediaChannel] = []
        self._lock = asyncio.Lock()
        
        # Initialize the OS routing layers
        PulseAudioRouter.initialize_cables(self.capacity)
        
        for i in range(self.capacity):
            chan = MediaChannel(i, self.config, self.loop, inbound_handler)
            chan.boot_subprocess()
            self.channels.append(chan)

    async def lease_idle_channel(self) -> MediaChannel | None:
        async with self._lock:
            for chan in self.channels:
                if not chan.is_busy:
                    chan.is_busy = True # Mark instantly to prevent race conditions
                    return chan
        return None

    def shutdown_all(self):
        for chan in self.channels:
            chan.shutdown()