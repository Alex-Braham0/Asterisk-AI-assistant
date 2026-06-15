import asyncio
import time
import threading
import sounddevice as sd
from telephony.baresip_ctrl import BaresipController, BaresipCallInstance

class MediaEngine:
    def __init__(self, sip_ip: str, sip_port: int, username: str, password: str, on_call_callback, loop=None):
        self.sip_ip = sip_ip
        self.sip_port = sip_port
        self.username = username
        self.on_call_callback = on_call_callback
        self.main_loop = loop or asyncio.get_event_loop()
        
        self.ctrl = BaresipController("127.0.0.1", 4444, self._handle_baresip_event)
        
        self.active_call = None 
        self.active_call_id = None
        self.media_active = False
        self._is_running = False
        
        self.heartbeat_thread = None
        self.pbx_to_ai_queue = asyncio.Queue() 
        self.tx_buffer = bytearray()
        self.tx_lock = threading.Lock()

    def start(self):
        self._is_running = True
        self.ctrl.start()
        print(f"[MediaEngine] Baresip interface initialized for user: {self.username}")

    def stop(self):
        self._is_running = False
        if self.active_call:
            self.drop_call()
        self.ctrl.stop()
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=2.0)

    def _handle_baresip_event(self, event: dict):
        ev_type = event.get("type", "")
        call_id = event.get("id")

        peer_uri = event.get("peeruri", "")
        peer_num = peer_uri.split("@")[0].replace("sip:", "") if peer_uri.startswith("sip:") else "Unknown"
        peer_name = event.get("peerdisplayname", peer_num)

        if ev_type == "CALL_INCOMING" and event.get("direction") == "incoming":
            if self.active_call is not None:
                if self.active_call_id != call_id:
                    print(f"[MediaEngine] Agent busy. Ignoring concurrent call {call_id}.")
                    return

            self.active_call_id = call_id
            self.media_active = False
            self.active_call = BaresipCallInstance(peer_name, peer_num, call_id)
            
            print(f"[MediaEngine] Incoming call tracked: ID={call_id} from {peer_name} ({peer_num})")
            self.main_loop.call_soon_threadsafe(self.on_call_callback, self.active_call)

        elif ev_type == "CALL_ESTABLISHED" and call_id == self.active_call_id:
            self.media_active = True
            if self.active_call:
                self.main_loop.call_soon_threadsafe(self.active_call.answered_event.set)

        elif ev_type == "CALL_CLOSED" and call_id == self.active_call_id:
            print(f"[MediaEngine] Call tracking session terminated.")
            self.media_active = False
            if self.active_call:
                self.main_loop.call_soon_threadsafe(self.active_call.ended_event.set)
            self.active_call = None
            self.active_call_id = None

    def answer_call(self, call: BaresipCallInstance) -> bool:
        success = self.ctrl.send_cmd("accept")
        if not success:
            print("[MediaEngine] Aborting audio bridge: Baresip failed to accept the call.")
            return False

        while not self.pbx_to_ai_queue.empty():
            try:
                self.pbx_to_ai_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
                
        with self.tx_lock:
            self.tx_buffer.clear()
            
        self._is_running = True
        self.media_active = True
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()
        return True

    def make_outbound_call(self, target_extension: str) -> BaresipCallInstance | None:
        if self.active_call is not None:
            return None
            
        generated_id = f"out-{int(time.time())}"
        self.active_call_id = generated_id
        self.media_active = False
        self.active_call = BaresipCallInstance(target_extension, target_extension, generated_id)
        
        print(f"[MediaEngine] Spawning outbound track path to extension: {target_extension}")
        success = self.ctrl.send_cmd("dial", str(target_extension))
        if not success:
            self.active_call = None
            self.active_call_id = None
            return None
        return self.active_call

    def drop_call(self, call_id: str = None):
        if self.active_call != None:
            self.ctrl.send_cmd("hangup")
        self.active_call = None
        self.active_call_id = None

    def send_dtmf(self, digit: str):
        self.ctrl.send_dtmf_udp(digit)

    def transfer_call(self, target_extension: str):
        sip_uri = f"sip:{target_extension}@{self.sip_ip}"
        self.ctrl.send_cmd("transfer", sip_uri)

    def flush_tx_buffer(self):
        with self.tx_lock:
            self.tx_buffer.clear()

    def inject_audio(self, pcm_8k_bytes: bytes):
        with self.tx_lock:
            self.tx_buffer.extend(pcm_8k_bytes)

    def _heartbeat_loop(self):
        print("[MediaEngine] Software Audio Bridge Engaged (Callback Mode).")
        
        def audio_callback(indata, outdata, frames, time_info, status):
            required_bytes = frames * 2  
            ai_is_speaking = False
            
            # --- TX PHASE: AI -> PulseAudio ---
            with self.tx_lock:
                if len(self.tx_buffer) >= required_bytes:
                    outdata[:] = self.tx_buffer[:required_bytes]
                    del self.tx_buffer[:required_bytes]
                    ai_is_speaking = True  # Flag that audio is physically playing
                else:
                    outdata[:] = b'\x00' * required_bytes

            # --- RX PHASE: PulseAudio -> AI ---
            try:
                # STRICT ROUTING ISOLATION (HALF-DUPLEX)
                # If the AI is outputting audio, mute the mic to prevent the echo loop entirely.
                if ai_is_speaking:
                    muted_data = b'\x00' * len(bytes(indata))
                    self.main_loop.call_soon_threadsafe(self.pbx_to_ai_queue.put_nowait, muted_data)
                else:
                    self.main_loop.call_soon_threadsafe(self.pbx_to_ai_queue.put_nowait, bytes(indata))
            except asyncio.QueueFull:
                pass

        try:
            with sd.RawStream(
                samplerate=8000, 
                blocksize=160, 
                channels=1, 
                dtype='int16',
                callback=audio_callback,
                latency='low'
            ):
                while self._is_running and self.active_call and self.media_active:
                    time.sleep(0.1)
                    
        except Exception as e:
            print(f"[MediaEngine] Failed to open callback stream: {e}")