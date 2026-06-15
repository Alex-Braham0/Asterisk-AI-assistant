import asyncio
import time
from telephony.baresip_ctrl import BaresipController, BaresipCallInstance
from telephony.audio_bridge import AudioBridge

class MediaEngine:
    def __init__(self, sip_ip: str, sip_port: int, username: str, password: str, on_call_callback, loop: asyncio.AbstractEventLoop):
        self.sip_ip = sip_ip
        self.username = username
        self.on_call_callback = on_call_callback
        self.loop = loop  # Stored loop reference for thread-safe signaling
        
        self.ctrl = BaresipController("127.0.0.1", 4444, self._handle_baresip_event)
        self.audio = AudioBridge()
        
        self.active_calls = {} 
        self.pbx_to_ai_queue = self.audio.rx_queue

    def start(self):
        self.ctrl.start()
        print(f"[MediaEngine] Baresip interface initialized for user: {self.username}")

    def stop(self):
        self.ctrl.stop()
        self.audio.stop()

    def _handle_baresip_event(self, event: dict):
        ev_type = event.get("type", "")
        call_id = event.get("id")

        peer_uri = event.get("peeruri", "")
        peer_num = peer_uri.split("@")[0].replace("sip:", "") if peer_uri.startswith("sip:") else "Unknown"
        peer_name = event.get("peerdisplayname", peer_num)

        if ev_type == "CALL_INCOMING" and event.get("direction") == "incoming":
            print(f"[MediaEngine] Incoming call tracked: ID={call_id} from {peer_name} ({peer_num})")
            
            call_instance = BaresipCallInstance(peer_name, peer_num, call_id)
            self.active_calls[call_id] = call_instance
            
            self.on_call_callback(call_instance)

        elif ev_type == "CALL_ESTABLISHED":
            if call_id in self.active_calls:
                call_obj = self.active_calls[call_id]
                self.loop.call_soon_threadsafe(call_obj.answered_event.set)

        elif ev_type == "CALL_CLOSED":
            print(f"[MediaEngine] Event: Call {call_id} network dropped.")
            if call_id in self.active_calls:
                call_obj = self.active_calls[call_id]
                self.loop.call_soon_threadsafe(call_obj.ended_event.set)
                
                del self.active_calls[call_id]

    def answer_call(self, call: BaresipCallInstance) -> bool:
        success = self.ctrl.send_cmd("accept")
        if not success:
            print("[MediaEngine] Aborting audio bridge: Baresip failed to accept the call.")
            return False

        self.audio.start()
        return True

    def make_outbound_call(self, target_extension: str) -> BaresipCallInstance | None:
        generated_id = f"out-{int(time.time())}"
        call_instance = BaresipCallInstance(target_extension, target_extension, generated_id)
        
        self.active_calls[generated_id] = call_instance
        print(f"[MediaEngine] Spawning outbound track path to extension: {target_extension}")
        
        success = self.ctrl.send_cmd("dial", str(target_extension))
        if not success:
            del self.active_calls[generated_id]
            return None
            
        return call_instance

    def drop_call(self, call_id: str):
        self.ctrl.send_cmd("hangup")
        self.audio.stop()
        if call_id in self.active_calls:
            del self.active_calls[call_id]

    def send_dtmf(self, digit: str):
        self.ctrl.send_cmd("dtmf", digit)

    def transfer_call(self, target_extension: str):
        print(f"[MediaEngine] Executing Blind Transfer to Extension: {target_extension}")
        sip_uri = f"sip:{target_extension}@{self.sip_ip}"
        self.ctrl.send_cmd("transfer", sip_uri)

    def inject_audio(self, pcm_8k_bytes: bytes):
        self.audio.inject_tx(pcm_8k_bytes)