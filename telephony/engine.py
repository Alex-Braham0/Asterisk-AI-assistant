import os
import asyncio
import threading
import subprocess
import time
import sounddevice as sd
import numpy as np
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
        
        self.pbx_to_ai_queue = asyncio.Queue(maxsize=10)
        self.tx_buffer = bytearray()
        self.tx_lock = threading.Lock()
        self.audio_thread = None
        self._audio_running = False

        self.is_buffering = True
        self.JITTER_BUFFER_MIN = 1600

    def start(self):

        # 1. Start the single, persistent hardware stream thread
        self._audio_running = True
        self.audio_thread = threading.Thread(target=self._stream_worker, daemon=True)
        self.audio_thread.start()
        
        # 2. Configure Baresip environment
        os.environ["PULSE_SINK"] = "Baresip_Tx"
        os.environ["PULSE_SOURCE"] = "Baresip_Rx.monitor"
        
        env = os.environ.copy()
        env["PULSE_SINK"] = "Baresip_Rx"            
        env["PULSE_SOURCE"] = "Baresip_Tx.monitor"  

        subprocess.run(["pkill", "-9", "-x", "baresip"], stderr=subprocess.DEVNULL)
        time.sleep(2)

        cmd = ["baresip"]
        self.baresip_process = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if hasattr(self, '_baresip_watchdog'):
            self.main_loop.create_task(self._baresip_watchdog())
            
        time.sleep(1)
        self.ctrl.start()

        if self.baresip_process.poll() is None:
            self._print_ready()

    async def _baresip_watchdog(self):
        while True:
            if self.baresip_process and self.baresip_process.poll() is not None:
                exit_code = self.baresip_process.returncode
                print(f"\n[MediaEngine] ⚠️ Baresip terminated (Exit Code: {exit_code}). Initiating self-healing...")
                
                self.drop_call()
                self.ctrl.stop()
                
                subprocess.run(["pkill", "-9", "-x", "baresip"], stderr=subprocess.DEVNULL)
                await asyncio.sleep(2) 
                
                env = os.environ.copy()
                env["PULSE_SINK"] = "Baresip_Rx"            
                env["PULSE_SOURCE"] = "Baresip_Tx.monitor"
                
                self.baresip_process = subprocess.Popen(["baresip"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                await asyncio.sleep(1)
                self.ctrl.start()
                print("[MediaEngine] ✅ Self-healing complete. Ready for calls.")
                self._print_ready()
                
            await asyncio.sleep(1)

    def stop(self):
        self._audio_running = False
        if self.active_call: 
            self.drop_call()
        self.ctrl.stop()

        if self.audio_thread: 
            self.audio_thread.join(timeout=2.0)
        
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
            
            # --- NEW: Separate flags for speaker and mic ---
            self.active_call.is_answered = False 
            # -----------------------------------------------
            
            self.main_loop.call_soon_threadsafe(self.on_inbound_callback, self, self.active_call)

        elif ev_type == "CALL_ESTABLISHED":
            if self.active_call:
                
                with self.tx_lock:
                    self.tx_buffer = bytearray(b'\x00' * 2400) + self.tx_buffer

                self.active_call.is_answered = True
                self.active_call.mic_muted = True
                self.main_loop.call_soon_threadsafe(self.active_call.answered_event.set)
                # ------------------------------------------

        elif ev_type == "CALL_CLOSED":
            if self.active_call:
                self.active_call.is_answered = False

            with self.tx_lock:
                self.tx_buffer.clear()
                self.is_buffering = True
                
            call_ref = self.active_call
            self.active_call = None
            if call_ref:
                self.main_loop.call_soon_threadsafe(call_ref.ended_event.set)

    async def answer_call(self) -> bool:
        await asyncio.sleep(0.5)
        if not self.active_call:
            return False
        return await self.main_loop.run_in_executor(None, self.ctrl.send_cmd, "accept")

    def transfer_call(self, target: str):
        print(f"[MediaEngine] 🔀 Transferring call to: {target}")
        # Baresip uses the 'transfer' command followed by the SIP URI or extension
        threading.Thread(target=self.ctrl.send_cmd, args=("transfer", str(target)), daemon=True).start()

    def hold_call(self, state: bool):
        cmd = "hold" if state else "resume"
        print(f"[MediaEngine] ⏸️ Toggling call hold state: {cmd}")
        threading.Thread(target=self.ctrl.send_cmd, args=(cmd,), daemon=True).start()

    def make_outbound_call(self, target_extension: str):
        if self.active_call: 
            self.drop_call()
            time.sleep(0.5)
            
        generated_id = f"out-singleton-{int(time.time())}"
        self.active_call = BaresipCallInstance(target_extension, target_extension, generated_id)
        threading.Thread(target=self.ctrl.send_cmd, args=("dial", str(target_extension)), daemon=True).start()
        return self.active_call

    def drop_call(self):
        # Do NOT terminate audio here — keep the stream running
        threading.Thread(target=self.ctrl.send_cmd, args=("hangup",), daemon=True).start()
        
        with self.tx_lock:
            self.tx_buffer.clear()
            self.is_buffering = True

        call_ref = self.active_call
        self.active_call = None
        if call_ref:
            self.main_loop.call_soon_threadsafe(call_ref.ended_event.set)

    def flush_tx_buffer(self):
        with self.tx_lock: 
            self.tx_buffer.clear()

    def inject_audio(self, pcm_bytes: bytes):
        with self.tx_lock: 
            self.tx_buffer.extend(pcm_bytes)

    def _stream_worker(self):
        def callback(indata, outdata, frames, time_info, status):
            req_bytes = frames * 2  

            current_time = time.time()
            ai_output_active = False
                       
            # 1. AI OUTPUT TO CALLER (Starts instantly on answer)
            if not self.active_call or not getattr(self.active_call, 'is_answered', False):
                outdata[:] = b'\x00' * req_bytes
            else:
                with self.tx_lock:
                    if self.is_buffering:
                        if len(self.tx_buffer) >= self.JITTER_BUFFER_MIN:
                            self.is_buffering = False
                        else:
                            outdata[:] = b'\x00' * req_bytes
                            
                    if not self.is_buffering:
                        if len(self.tx_buffer) >= req_bytes:
                            outdata[:] = self.tx_buffer[:req_bytes]
                            del self.tx_buffer[:req_bytes]
                            ai_output_active = True
                            self.echo_tail_time = current_time
                            
                        elif len(self.tx_buffer) > 0:
                            # --- FIX: Drain final leftover bytes so buffer hits exactly 0 ---
                            rem = len(self.tx_buffer)
                            outdata[:rem] = self.tx_buffer[:]
                            outdata[rem:] = b'\x00' * (req_bytes - rem) # Pad the rest with silence
                            self.tx_buffer.clear() # Buffer is now exactly 0
                            ai_output_active = True
                            self.echo_tail_time = current_time
                            self.is_buffering = True
                            
                        else:
                            outdata[:] = b'\x00' * req_bytes
                            self.is_buffering = True

            # 2. CALLER INPUT TO AI (Muted for 1.5s to prevent interruption)
            if not self.active_call or getattr(self.active_call, 'mic_muted', True):
                # Silence while AI is outputting initial greeting
                clean_mic = b'\x00' * req_bytes
            else:
                unmute_time = getattr(self.active_call, 'unmute_time', 0)
                time_since_unmute = time.time() - unmute_time
                FADE_DURATION = 1.5  # 1 second smooth fade-up

                # Convert to float and strip DC offset
                mic_audio = np.frombuffer(indata, dtype=np.int16).astype(np.float32)
                mic_audio -= np.mean(mic_audio)

                if time_since_unmute < FADE_DURATION:
                    multiplier = time_since_unmute / FADE_DURATION
                    faded_audio = mic_audio * multiplier
                    clean_mic = faded_audio.astype(np.int16).tobytes()
                else:
                    clean_mic = mic_audio.astype(np.int16).tobytes()
                    
            self.main_loop.call_soon_threadsafe(self._safe_enqueue, clean_mic)

        try:
            with sd.RawStream(samplerate=8000, blocksize=320, channels=1, dtype='int16', callback=callback, latency='low'):
                while self._audio_running: 
                    time.sleep(0.05)
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

    def ungate_mic(self):
        """Called when Gemini fires turnComplete to begin un-muting the caller."""
        if self.active_call:
            self.active_call.mic_muted = False
            self.active_call.unmute_time = time.time()
            print("[MediaEngine] 🔓 Gemini finished greeting. Starting mic fade-up.")