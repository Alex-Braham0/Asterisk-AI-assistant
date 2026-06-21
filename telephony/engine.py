import os
import asyncio
import threading
import subprocess
import time
import sounddevice as sd
from telephony.baresip_ctrl import BaresipController, BaresipCallInstance

class MediaEngine:
    def __init__(self, config, loop, on_inbound_callback):
        self.config = config
        self.main_loop = loop
        self.on_inbound_callback = on_inbound_callback
        
        self.ctrl_port = 4444
        self.ctrl = BaresipController("127.0.0.1", self.ctrl_port, self._handle_event)
        
        self.baresip_process = None
        self.active_call = None
        
        self.pbx_to_ai_queue = asyncio.Queue()
        self.tx_buffer = bytearray()
        self.tx_lock = threading.Lock()
        self.audio_thread = None
        self._audio_running = False

    def _init_virtual_cables(self):
        print("[MediaEngine] Initializing PulseAudio virtual cables...")
        import subprocess
        subprocess.run("pactl list short modules | grep null-sink | cut -f1 | xargs -L1 pactl unload-module", shell=True, stderr=subprocess.DEVNULL)
        
        tx_result = subprocess.run(["pactl", "load-module", "module-null-sink", "sink_name=Baresip_Tx", "sink_properties=device.description=Baresip_Tx"], capture_output=True, text=True)
        rx_result = subprocess.run(["pactl", "load-module", "module-null-sink", "sink_name=Baresip_Rx", "sink_properties=device.description=Baresip_Rx"], capture_output=True, text=True)
        
        if tx_result.returncode != 0 or rx_result.returncode != 0:
            raise RuntimeError(f"FATAL: PulseAudio cable allocation failed.\nTx Error: {tx_result.stderr}\nRx Error: {rx_result.stderr}")

    def start(self):
        self._init_virtual_cables()
        print("[MediaEngine] Booting Singleton Baresip Engine...")
        
        # Original static routing
        os.environ["PULSE_SINK"] = "Baresip_Tx"
        os.environ["PULSE_SOURCE"] = "Baresip_Rx.monitor"
        
        env = os.environ.copy()
        env["PULSE_SINK"] = "Baresip_Rx"            
        env["PULSE_SOURCE"] = "Baresip_Tx.monitor"  

        import subprocess
        cmd = ["baresip"]
        self.baresip_process = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Keep the watchdog, it does not affect audio
        if hasattr(self, '_baresip_watchdog'):
            self.main_loop.create_task(self._baresip_watchdog())
            
        import time
        time.sleep(1)
        self.ctrl.start()

    async def _baresip_watchdog(self):
        """Polls the subprocess. If it dies, force systemd to restart the app."""
        while self._audio_running or self.baresip_process:
            if self.baresip_process and self.baresip_process.poll() is not None:
                exit_code = self.baresip_process.returncode
                print(f"[FATAL] Baresip subprocess crashed unexpectedly (Exit Code: {exit_code}).")
                
                # Flush state
                self.drop_call()
                self._stop_audio_stream()
                
                # Force kill the main Python process. 
                # systemd will catch this and execute a clean application reboot.
                import sys
                sys.exit(1)
                
            await asyncio.sleep(1)

    def stop(self):
        if self.active_call: 
            self.drop_call()
        self.ctrl.stop()
        self._stop_audio_stream()
        
        if self.baresip_process:
            self.baresip_process.terminate()
            try:
                # Wait up to 3 seconds for graceful shutdown
                self.baresip_process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                print("[MediaEngine] Baresip failed to terminate gracefully. Force killing.")
                self.baresip_process.kill()
                self.baresip_process.wait()

    def _handle_event(self, event: dict):
        ev_type = event.get("type", "")
        call_id = event.get("id")

        if ev_type == "CALL_INCOMING":
            # If the engine is busy (e.g., AI is on a background mission), drop the call instantly
            if self.active_call:
                self.ctrl.send_cmd("hangup")
                return
                
            peer_uri = event.get("peeruri", "")
            peer_num = peer_uri.split("@")[0].replace("sip:", "")
            self.active_call = BaresipCallInstance(event.get("peerdisplayname", peer_num), peer_num, call_id)
            self.main_loop.call_soon_threadsafe(self.on_inbound_callback, self, self.active_call)

        elif ev_type == "CALL_ESTABLISHED":
            if self.active_call:
                self.main_loop.call_soon_threadsafe(self.active_call.answered_event.set)
                self._start_audio_stream()

        elif ev_type == "CALL_CLOSED":
            self._stop_audio_stream()
            if self.active_call:
                self.main_loop.call_soon_threadsafe(self.active_call.ended_event.set)
                self.active_call = None

    async def answer_call(self) -> bool:
        # Give Baresip 500ms to stabilize the new SIP channel before commanding it to answer.
        # This prevents the race condition when you hang up and call back immediately.
        await asyncio.sleep(0.5)
        
        # Return the True/False state directly from the BaresipController
        return self.ctrl.send_cmd("accept")

    def make_outbound_call(self, target_extension: str):
        if self.active_call: 
            self.drop_call()
            import time
            time.sleep(0.5)
            
        generated_id = f"out-singleton-{int(time.time())}"
        self.active_call = BaresipCallInstance(target_extension, target_extension, generated_id)
        
        # FIX: Fire and forget via a background thread to prevent Baresip socket timeouts from gaslighting the AI
        import threading
        threading.Thread(target=self.ctrl.send_cmd, args=("dial", str(target_extension)), daemon=True).start()
        
        return self.active_call

    def drop_call(self):
        import threading
        threading.Thread(target=self.ctrl.send_cmd, args=("hangup",), daemon=True).start()
        
        # Forcefully clear the engine lock instantly so subsequent headless agents don't get blocked
        if self.active_call:
            self.main_loop.call_soon_threadsafe(self.active_call.ended_event.set)
            self.active_call = None

    def flush_tx_buffer(self):
        with self.tx_lock: self.tx_buffer.clear()

    def inject_audio(self, pcm_bytes: bytes):
        with self.tx_lock: self.tx_buffer.extend(pcm_bytes)

    def _start_audio_stream(self):
        if self._audio_running: return
        self._audio_running = True
        
        while not self.pbx_to_ai_queue.empty():
            try: self.pbx_to_ai_queue.get_nowait()
            except asyncio.QueueEmpty: break
            
        self.audio_thread = threading.Thread(target=self._stream_worker, daemon=True)
        self.audio_thread.start()

    def _stop_audio_stream(self):
        self._audio_running = False
        if self.audio_thread: self.audio_thread.join(timeout=1.0)

    def _stream_worker(self):
        def callback(indata, outdata, frames, time_info, status):
            req_bytes = frames * 2  
            ai_speaking = False
            
            with self.tx_lock:
                if len(self.tx_buffer) >= req_bytes:
                    outdata[:] = self.tx_buffer[:req_bytes]
                    del self.tx_buffer[:req_bytes]
                    ai_speaking = True
                else:
                    outdata[:] = b'\x00' * req_bytes
                    
            if not ai_speaking:
                try: 
                    self.main_loop.call_soon_threadsafe(self.pbx_to_ai_queue.put_nowait, bytes(indata))
                except asyncio.QueueFull: 
                    pass

        try:
            # Using device=None forces Python to use the OS default.
            # Because we set the PULSE_SINK/SOURCE environment variables, the OS will perfectly steer it into our virtual cables.
            with sd.RawStream(samplerate=8000, blocksize=160, channels=1, dtype='int16', callback=callback, latency='low'):
                while self._audio_running: time.sleep(0.1)
        except Exception as e:
            print(f"[MediaEngine] Audio stream crash: {e}")

    def _safe_enqueue(self, pcm_data: bytes):
        """Executes safely INSIDE the asyncio event loop to manage queue capacity."""
        try:
            self.pbx_to_ai_queue.put_nowait(pcm_data)
        except asyncio.QueueFull:
            try:
                self.pbx_to_ai_queue.get_nowait() # Discard the oldest 20ms frame
                self.pbx_to_ai_queue.put_nowait(pcm_data)
            except asyncio.QueueEmpty:
                pass