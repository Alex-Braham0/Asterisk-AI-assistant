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

        # Target names for the PulseAudio Virtual Cables
        self.tx_name = "Baresip_Tx"
        self.rx_name = "Baresip_Rx.monitor"

    def _init_virtual_cables(self):
        print("[MediaEngine] Initializing PulseAudio virtual cables...")
        # Purge dead cables from previous crashes
        subprocess.run("pactl list short modules | grep null-sink | cut -f1 | xargs -L1 pactl unload-module", shell=True, stderr=subprocess.DEVNULL)
        # Allocate fresh virtual cables
        subprocess.run(["pactl", "load-module", "module-null-sink", "sink_name=Baresip_Tx", "sink_properties=device.description=Baresip_Tx"], stdout=subprocess.DEVNULL)
        subprocess.run(["pactl", "load-module", "module-null-sink", "sink_name=Baresip_Rx", "sink_properties=device.description=Baresip_Rx"], stdout=subprocess.DEVNULL)

    def _get_device_indices(self) -> tuple[int, int]:
        devices = sd.query_devices()
        in_idx = out_idx = None
        
        for idx, dev in enumerate(devices):
            if self.rx_name in dev['name'] and dev['max_input_channels'] > 0:
                in_idx = idx
            if "Baresip_Tx" in dev['name'] and dev['max_output_channels'] > 0:
                out_idx = idx
                
        if in_idx is None or out_idx is None:
            raise RuntimeError(f"PulseAudio devices {self.rx_name} / Baresip_Tx not found by sounddevice. Ensure PulseAudio is running.")
            
        return in_idx, out_idx

    def start(self):
        self._init_virtual_cables()
        print("[MediaEngine] Booting Singleton Baresip Engine...")
        
        # Inject PulseAudio routing directly into the Baresip process
        env = os.environ.copy()
        env["PULSE_SINK"] = "Baresip_Rx"            # Baresip outputs human audio to Rx
        env["PULSE_SOURCE"] = "Baresip_Tx.monitor"  # Baresip listens to AI audio from Tx

        cmd = ["baresip"]
        self.baresip_process = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        self.ctrl.start()

    def stop(self):
        if self.active_call: self.drop_call()
        self.ctrl.stop()
        if self.baresip_process:
            self.baresip_process.terminate()

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

    def answer_call(self):
        self.ctrl.send_cmd("accept")

    def make_outbound_call(self, target_extension: str):
        if self.active_call: return None
        generated_id = f"out-singleton-{int(time.time())}"
        self.active_call = BaresipCallInstance(target_extension, target_extension, generated_id)
        if not self.ctrl.send_cmd("dial", str(target_extension)):
            self.active_call = None
            return None
        return self.active_call

    def drop_call(self):
        self.ctrl.send_cmd("hangup")

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
            
        in_idx, out_idx = self._get_device_indices()
        
        self.audio_thread = threading.Thread(target=self._stream_worker, args=(in_idx, out_idx), daemon=True)
        self.audio_thread.start()

    def _stop_audio_stream(self):
        self._audio_running = False
        if self.audio_thread: self.audio_thread.join(timeout=1.0)

    def _stream_worker(self, in_idx, out_idx):
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
                try: self.main_loop.call_soon_threadsafe(self.pbx_to_ai_queue.put_nowait, bytes(indata))
                except asyncio.QueueFull: pass

        try:
            # Bind exclusively to the PulseAudio virtual cables
            with sd.RawStream(device=(in_idx, out_idx), samplerate=8000, blocksize=160, channels=1, dtype='int16', callback=callback, latency='low'):
                while self._audio_running: time.sleep(0.1)
        except Exception as e:
            print(f"[MediaEngine] Audio stream crash: {e}")