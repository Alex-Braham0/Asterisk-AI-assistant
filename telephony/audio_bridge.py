import threading
import queue
import time
import sounddevice as sd

class AudioBridge:
    def __init__(self, sample_rate=8000, blocksize=160, channels=1):
        self.sample_rate = sample_rate
        self.blocksize = blocksize
        self.channels = channels
        
        # Pure thread-safe queue eliminates cross-thread asyncio scheduling loop jitter
        self.rx_queue = queue.Queue(maxsize=2000)  # PBX -> AI (Mic)
        self.tx_buffer = bytearray()               # AI -> PBX (Speaker)
        self.tx_lock = threading.Lock()
        
        self._is_running = False
        self.heartbeat_thread = None

    def start(self):
        if self._is_running:
            return
            
        with self.tx_lock:
            self.tx_buffer.clear()
            
        while not self.rx_queue.empty():
            try:
                self.rx_queue.get_nowait()
            except queue.Empty:
                break
                
        self._is_running = True
        self.heartbeat_thread = threading.Thread(target=self._audio_stream_loop, daemon=True)
        self.heartbeat_thread.start()
        print("[AudioBridge] ALSA/PulseAudio hardware streaming engaged.")

    def stop(self):
        self._is_running = False
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=2.0)
        print("[AudioBridge] ALSA/PulseAudio hardware streaming terminated.")

    def flush_tx_buffer(self):
        with self.tx_lock:
            self.tx_buffer.clear()

    def inject_tx(self, pcm_8k_bytes: bytes):
        with self.tx_lock:
            self.tx_buffer.extend(pcm_8k_bytes)

    def _audio_stream_loop(self):
        def audio_callback(indata, outdata, frames, time_info, status):
            # --- RX PHASE: PulseAudio -> AI ---
            try:
                self.rx_queue.put_nowait(bytes(indata))
            except queue.Full:
                pass

            # --- TX PHASE: AI -> PulseAudio ---
            required_bytes = frames * 2  # 16-bit Mono = 2 bytes per frame
            with self.tx_lock:
                if len(self.tx_buffer) >= required_bytes:
                    outdata[:] = self.tx_buffer[:required_bytes]
                    del self.tx_buffer[:required_bytes]
                else:
                    outdata[:] = b'\x00' * required_bytes

        try:
            with sd.RawStream(
                samplerate=self.sample_rate, 
                blocksize=self.blocksize, 
                channels=self.channels, 
                dtype='int16',
                callback=audio_callback,
                latency='low'
            ):
                while self._is_running:
                    time.sleep(0.1)
        except Exception as e:
            print(f"[AudioBridge Info] Telephony stream interface released: {e}")