import os
import asyncio
import threading
import subprocess
import time
import sounddevice as sd
from telephony.baresip_ctrl import BaresipController, BaresipCallInstance
from telephony.audio_router import PulseAudioRouter

class MediaChannel:
    def __init__(self, channel_id: int, config, loop, on_inbound_callback):
        self.channel_id = channel_id
        self.config = config
        self.main_loop = loop
        self.on_inbound_callback = on_inbound_callback
        
        self.ctrl_port = 4440 + channel_id
        self.tx_name = f"Baresip_Tx_{channel_id}"
        self.rx_name = f"Baresip_Rx_{channel_id}.monitor"
        
        self.ctrl = BaresipController("127.0.0.1", self.ctrl_port, self._handle_event)
        self.baresip_process = None
        
        self.active_call = None
        self.is_busy = False
        
        self.pbx_to_ai_queue = asyncio.Queue()
        self.tx_buffer = bytearray()
        self.tx_lock = threading.Lock()
        self.audio_thread = None
        self._audio_running = False

    def boot_subprocess(self):
        env = os.environ.copy()
        env["PULSE_SINK"] = self.tx_name
        env["PULSE_SOURCE"] = self.rx_name
        
        cmd = [
            "baresip", 
            "-e", f"/sip_ip {self.config.sip_ip}",
            "-e", f"/ctrl_tcp_port {self.ctrl_port}"
        ]
        
        self.baresip_process = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1) 
        self.ctrl.start()
        print(f"[Channel {self.channel_id}] Baresip subprocess online (Port {self.ctrl_port}).")

    def shutdown(self):
        if self.active_call:
            self.drop_call()
        self.ctrl.stop()
        if self.baresip_process:
            self.baresip_process.terminate()
            self.baresip_process.wait()

    def _handle_event(self, event: dict):
        ev_type = event.get("type", "")
        call_id = event.get("id")

        if ev_type == "CALL_INCOMING":
            if self.is_busy: return 
            self.is_busy = True
            peer_uri = event.get("peeruri", "")
            peer_num = peer_uri.split("@")[0].replace("sip:", "")
            self.active_call = BaresipCallInstance(event.get("peerdisplayname", peer_num), peer_num, call_id)
            self.main_loop.call_soon_threadsafe(self.on_inbound_callback, self, self.active_call)

        elif ev_type == "CALL_ESTABLISHED":
            if self.active_call:
                self.main_loop.call_soon_threadsafe(self.active_call.answered_event.set)
                self._start_audio_stream()

        elif ev_type == "CALL_CLOSED":
            self.is_busy = False
            self._stop_audio_stream()
            if self.active_call:
                self.main_loop.call_soon_threadsafe(self.active_call.ended_event.set)
                self.active_call = None

    def answer_call(self):
        self.ctrl.send_cmd("accept")

    def make_outbound_call(self, target_extension: str):
        self.is_busy = True
        generated_id = f"out-{self.channel_id}-{int(time.time())}"
        self.active_call = BaresipCallInstance(target_extension, target_extension, generated_id)
        
        if not self.ctrl.send_cmd("dial", str(target_extension)):
            self.is_busy = False
            self.active_call = None
            return None
        return self.active_call

    def drop_call(self):
        self.ctrl.send_cmd("hangup")

    def transfer_call(self, target_extension: str):
        sip_uri = f"sip:{target_extension}@{self.config.sip_ip}"
        self.ctrl.send_cmd("transfer", sip_uri)

    def send_dtmf(self, digit: str):
        self.ctrl.send_dtmf_udp(digit)

    def flush_tx_buffer(self):
        with self.tx_lock: self.tx_buffer.clear()

    def inject_audio(self, pcm_bytes: bytes):
        with self.tx_lock: self.tx_buffer.extend(pcm_bytes)

    def _start_audio_stream(self):
        if self._audio_running: return
        self._audio_running = True
        
        while not self.pbx_to_ai_queue.empty():
            try:
                self.pbx_to_ai_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            
        in_idx, out_idx = PulseAudioRouter.get_sd_device_indices(self.tx_name, self.rx_name)
        
        self.audio_thread = threading.Thread(target=self._stream_worker, args=(in_idx, out_idx), daemon=True)
        self.audio_thread.start()

    def _stop_audio_stream(self):
        self._audio_running = False
        if self.audio_thread:
            self.audio_thread.join(timeout=1.0)

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
                try:
                    self.main_loop.call_soon_threadsafe(self.pbx_to_ai_queue.put_nowait, bytes(indata))
                except asyncio.QueueFull:
                    pass

        try:
            with sd.RawStream(device=(in_idx, out_idx), samplerate=8000, blocksize=160, channels=1, dtype='int16', callback=callback, latency='low'):
                while self._audio_running: time.sleep(0.1)
        except Exception as e:
            print(f"[Channel {self.channel_id}] Audio stream crash: {e}")