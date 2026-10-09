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
        
        self.ctrl_port = 5444
        self.ctrl = BaresipController("127.0.0.1", self.ctrl_port, self._handle_event)
        
        self.baresip_process = None
        self.active_call = None
        
        self.pbx_to_ai_queue = asyncio.Queue(maxsize=100)
        self.tx_buffer = bytearray()
        self.tx_lock = threading.Lock()
        self.audio_thread = None
        self._audio_running = False

        self.is_buffering = True
        self.JITTER_BUFFER_MIN = 1600

    def _init_virtual_cables(self):
        print("[MediaEngine] Checking PulseAudio virtual cables...")
        
        def attempt_allocation():
            subprocess.run("pactl list short modules | grep null-sink | cut -f1 | xargs -L1 pactl unload-module", shell=True, stderr=subprocess.DEVNULL)
            tx = subprocess.run(["pactl", "load-module", "module-null-sink", "sink_name=Baresip_Tx", "sink_properties=device.description=Baresip_Tx"], capture_output=True, text=True)
            rx = subprocess.run(["pactl", "load-module", "module-null-sink", "sink_name=Baresip_Rx", "sink_properties=device.description=Baresip_Rx"], capture_output=True, text=True)
            return tx, rx

        tx_result, rx_result = attempt_allocation()
        
        # Auto-heal PulseAudio if it is dead or unresponsive
        if tx_result.returncode != 0 or rx_result.returncode != 0:
            print("[MediaEngine] PulseAudio daemon offline. Restarting audio server...")
            subprocess.run(["pulseaudio", "-k"], stderr=subprocess.DEVNULL)
            time.sleep(1)
            subprocess.run(["pulseaudio", "--start"], stderr=subprocess.DEVNULL)
            time.sleep(2)
            import sounddevice as sd
            sd._terminate()
            sd._initialize()
            
            tx_result, rx_result = attempt_allocation()
            if tx_result.returncode != 0 or rx_result.returncode != 0:
                raise RuntimeError(f"FATAL: PulseAudio cable allocation failed after restart.\nTx: {tx_result.stderr}\nRx: {rx_result.stderr}")
                
        print("[MediaEngine] Audio virtual cables allocated successfully.")

    def start(self):
        self._init_virtual_cables()
        
        os.environ["PULSE_SINK"] = "Baresip_Tx"
        os.environ["PULSE_SOURCE"] = "Baresip_Rx.monitor"
        
        env = os.environ.copy()
        env["PULSE_SINK"] = "Baresip_Rx"            
        env["PULSE_SOURCE"] = "Baresip_Tx.monitor"  

        subprocess.run(["pkill", "-9", "-x", "baresip"], stderr=subprocess.DEVNULL)
        time.sleep(2)

        cmd = ["baresip"]
        # Mute Baresip's C-level stdout and stderr
        self.baresip_process = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if hasattr(self, '_baresip_watchdog'):
            self.main_loop.create_task(self._baresip_watchdog())
            
        time.sleep(1)
        self.ctrl.start()

        # Only print ready if Baresip didn't instantly crash from a port conflict
        if self.baresip_process.poll() is None:
            self._print_ready()

    async def _baresip_watchdog(self):
        while True:
            if self.baresip_process and self.baresip_process.poll() is not None:
                exit_code = self.baresip_process.returncode
                print(f"\n[MediaEngine] ⚠️ Baresip terminated (Exit Code: {exit_code}). Initiating self-healing...")
                
                self.drop_call()
                self._stop_audio_stream()
                self.ctrl.stop()
                
                subprocess.run(["pkill", "-9", "-x", "baresip"], stderr=subprocess.DEVNULL)
                await asyncio.sleep(2) 
                
                env = os.environ.copy()
                env["PULSE_SINK"] = "Baresip_Rx"            
                env["PULSE_SOURCE"] = "Baresip_Tx.monitor"
                
                # Mute the rebooted instance as well
                self.baresip_process = subprocess.Popen(["baresip"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                await asyncio.sleep(1)
                self.ctrl.start()
                print("[MediaEngine] ✅ Self-healing complete. Ready for calls.")
                self._print_ready()
                
            await asyncio.sleep(1)

    def stop(self):
        if self.active_call: 
            self.drop_call()
        self.ctrl.stop()
        self._stop_audio_stream()
        
        if self.baresip_process:
            self.baresip_process.terminate()
            try:
                self.baresip_process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self.baresip_process.kill()
                self.baresip_process.wait()

    def _handle_event(self, event: dict):
        ev_type = event.get("type", "")
        call_id = event.get("id")

        if ev_type == "CALL_INCOMING":
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
            call_ref = self.active_call
            self.active_call = None
            if call_ref:
                self.main_loop.call_soon_threadsafe(call_ref.ended_event.set)

    async def answer_call(self) -> bool:
        await asyncio.sleep(0.5)
        if not self.active_call:
            return False
        return await self.main_loop.run_in_executor(None, self.ctrl.send_cmd, "accept")

    def make_outbound_call(self, target_extension: str):
        if self.active_call: 
            self.drop_call()
            time.sleep(0.5)
            
        generated_id = f"out-singleton-{int(time.time())}"
        self.active_call = BaresipCallInstance(target_extension, target_extension, generated_id)
        threading.Thread(target=self.ctrl.send_cmd, args=("dial", str(target_extension)), daemon=True).start()
        return self.active_call

    def drop_call(self):
        self._stop_audio_stream()
        threading.Thread(target=self.ctrl.send_cmd, args=("hangup",), daemon=True).start()
        
        call_ref = self.active_call
        self.active_call = None
        if call_ref:
            self.main_loop.call_soon_threadsafe(call_ref.ended_event.set)

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
            
            with self.tx_lock:
                # 1. Buffering State: Play silence until the Jitter Buffer is full
                if self.is_buffering:
                    if len(self.tx_buffer) >= self.JITTER_BUFFER_MIN:
                        self.is_buffering = False
                    else:
                        outdata[:] = b'\x00' * req_bytes
                        self.main_loop.call_soon_threadsafe(self._safe_enqueue, bytes(indata))
                        return

                # 2. Playback State: Jitter buffer is full, feed the hardware
                if len(self.tx_buffer) >= req_bytes:
                    outdata[:] = self.tx_buffer[:req_bytes]
                    del self.tx_buffer[:req_bytes]
                else:
                    # 3. Starvation State: Network dropped entirely. Output silence and re-buffer
                    outdata[:] = b'\x00' * req_bytes
                    self.is_buffering = True
                    
            self.main_loop.call_soon_threadsafe(self._safe_enqueue, bytes(indata))

        try:
            with sd.RawStream(samplerate=8000, blocksize=320, channels=1, dtype='int16', callback=callback, latency=0.2):
                while self._audio_running: time.sleep(0.05)
        except Exception as e:
            print(f"[MediaEngine] Audio stream crash: {e}")

    def _safe_enqueue(self, pcm_data: bytes):
        try:
            self.pbx_to_ai_queue.put_nowait(pcm_data)
        except asyncio.QueueFull:
            try:
                self.pbx_to_ai_queue.get_nowait() 
                self.pbx_to_ai_queue.put_nowait(pcm_data)
            except asyncio.QueueEmpty:
                pass

    def _print_ready(self):
        print("\n" + "="*50)
        print(">>> [SYSTEM READY] Listening for inbound calls... <<<")
        print("="*50 + "\n")