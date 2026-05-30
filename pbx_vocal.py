import threading
import queue
import socket
import json
import time
import asyncio
import traceback
import sounddevice as sd
import numpy as np

class CallState:
    ANSWERED = "ANSWERED"
    ENDED = "ENDED"
    RINGING = "RINGING"

class MockSIPRequest:
    def __init__(self, caller_name, caller_number):
        self.headers = {
            'From': {
                'caller': str(caller_name),
                'number': str(caller_number).strip()
            }
        }

class BaresipCallInstance:
    def __init__(self, caller_name, caller_number, call_id, parent_engine):
        self._id = call_id
        self._engine = parent_engine
        self.request = MockSIPRequest(caller_name, caller_number)
        self.state = self

        self.answered_event = asyncio.Event()
        self.ended_event = asyncio.Event()

    @property
    def name(self):
        if self._engine.active_call_id == self._id:
            if self._engine.media_active:
                return "ANSWERED"
            return "RINGING"
        return "ENDED"

    def answer(self):
        pass

    def deny(self):
        self._engine.drop_call()

class MediaEngine:
    def __init__(self, sip_ip, sip_port, username, password, on_call_callback, my_ip):
        self.username = username
        self.on_call_callback = on_call_callback
        self.main_loop = asyncio.get_event_loop()
        
        self.sip_ip = sip_ip
        self.sip_port = sip_port

        self.ctrl_host = "127.0.0.1"
        self.ctrl_port = 4444

        # ALSA Device Mapping (Mirroring the Baresip config)
        self.alsa_rx_device = None#'Baresip_Rx.monitor' # Listens to Baresip's output
        self.alsa_tx_device = None#'Baresip_Tx' # Speaks to Baresip's input

        self.active_call = None 
        self.active_call_id = None
        self.media_active = False
        self._is_running = False
        
        self.heartbeat_thread = None
        self.ctrl_listener_thread = None
        
        self.pbx_to_ai_queue = asyncio.Queue() 
        self.tx_buffer = bytearray()
        self.tx_lock = threading.Lock()

    def start(self):
        self._is_running = True
        self.ctrl_listener_thread = threading.Thread(target=self._ctrl_listener_loop, daemon=True)
        self.ctrl_listener_thread.start()
        print(f"[MediaEngine] Baresip interface initialized for user: {self.username}")

    def stop(self):
        self._is_running = False
        if self.active_call:
            self.drop_call()
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=2.0)
        if self.ctrl_listener_thread:
            self.ctrl_listener_thread.join(timeout=2.0)

    def _send_cmd(self, command, params=""):
        """Sends a command using a guaranteed fresh socket with retry logic."""
        payload = {"command": command.strip().replace('/', ''), "params": params.strip()}
        json_payload = json.dumps(payload)
        netstring_payload = f"{len(json_payload)}:{json_payload},"

        max_retries = 3
        for attempt in range(max_retries):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1.0)
                    s.connect((self.ctrl_host, self.ctrl_port))
                    s.sendall(netstring_payload.encode('utf-8'))
                    
                    # Force wait for a response to confirm Baresip processed it
                    response = s.recv(2048)
                    if not response:
                        print(f"[MediaEngine] Command '{command}' attempt {attempt+1} failed: Empty response.")
                        time.sleep(0.2)
                        continue
                    return True
            except Exception as e:
                print(f"[MediaEngine] Command '{command}' exception on attempt {attempt+1}: {e}")
                time.sleep(0.2)
                
        print(f"[MediaEngine] CRITICAL: Failed to send command '{command}' after {max_retries} attempts.")
        return False

    def _ctrl_listener_loop(self):
        while self._is_running:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((self.ctrl_host, self.ctrl_port))
                buffer = ""
                while self._is_running:
                    data = s.recv(4096).decode('utf-8', errors='ignore')
                    if not data: break
                    buffer += data
                    while True:
                        if not buffer or ":" not in buffer: break
                        try:
                            len_str, remaining = buffer.split(":", 1)
                            length = int(len_str)
                        except ValueError:
                            buffer = buffer[1:]
                            continue
                        if len(remaining) < (length + 1): break
                        json_payload = remaining[:length]
                        buffer = remaining[length + 1:] 
                        try:
                            event = json.loads(json_payload)
                            if isinstance(event, dict):
                                self._handle_baresip_event(event)
                        except json.JSONDecodeError: continue
                s.close()
            except Exception as e:
                print(f"\n[MediaEngine CRITICAL] Control Loop Exception: {e}")
                traceback.print_exc()
                time.sleep(1)

    def _handle_baresip_event(self, event):
        ev_type = event.get("type", "")
        call_id = event.get("id")

        peer_uri = event.get("peeruri", "")
        peer_num = "Unknown"
        if peer_uri.startswith("sip:"):
            peer_num = peer_uri.split("@")[0].replace("sip:", "")
            
        peer_name = event.get("peerdisplayname", peer_num)

        if ev_type == "CALL_DTMF_START":
            digit = event.get("param", "Unknown")
            print(f"\n[MediaEngine] ☎️ KEYPAD PRESS RECEIVED: {digit}")
            # Later, we can inject this into the AI's queue!
            return
        elif ev_type == "CALL_DTMF_END":
            return # Silently consume the end event so it doesn't trigger other logic

        if event.get("direction") == "outgoing":
            if self.active_call is not None and str(self.active_call_id).startswith("out-"):
                print(f"[MediaEngine] Outbound Call ID locked: {call_id}")
                self.active_call_id = call_id
                self.active_call._id = call_id
        
        if ev_type == "CALL_INCOMING" and event.get("direction") == "incoming":
    
            # Are we already talking to someone?
            if self.active_call is not None:
                if self.active_call_id != call_id:
                    # We are busy. Swallow the event and do absolutely nothing.
                    print(f"[MediaEngine] Agent busy. Ignoring concurrent call {call_id}. Letting it ring.")
                    return
                    
            # We are free. Lock the agent and track the new call.
            self.active_call_id = call_id
            self.media_active = False
            self.active_call = BaresipCallInstance(peer_name, peer_num, call_id, self)
            
            print(f"[MediaEngine] Incoming call tracked: ID={call_id} from {peer_name} ({peer_num})")
            self.main_loop.call_soon_threadsafe(self.on_call_callback, self.active_call)

        elif ev_type == "CALL_LOCAL_SDP" and event.get("direction") == "incoming":
            if self.active_call_id == call_id:
                print(f"[MediaEngine] Processing SDP for active call: {call_id}")

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

    def send_dtmf(self, digit):
        """Bypasses ctrl_tcp and injects DTMF via the UDP console module."""
        if self.active_call is not None:
            # Extract exactly one character
            clean_digit = str(digit).strip()[0]
            print(f"[MediaEngine] Injecting DTMF Tone via UDP: {clean_digit}")
            
            try:
                # Fire the raw character at Baresip's UDP console
                udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                udp_sock.sendto(clean_digit.encode('utf-8'), ("127.0.0.1", 5555))
                udp_sock.close()
            except Exception as e:
                print(f"[MediaEngine] Failed to send UDP DTMF: {e}")
        else:
            print("[MediaEngine] Cannot send DTMF: No active call.")

    def transfer_call(self, target_extension):
        """Executes a SIP REFER to blind-transfer the active call."""
        if self.active_call is not None:
            print(f"[MediaEngine] Executing Blind Transfer to Extension: {target_extension}")
            # Baresip command for blind transfer is 'transfer <uri>'
            sip_uri = f"sip:{target_extension}@{self.sip_ip}"
            self._send_cmd("transfer", sip_uri)
            
            # The PBX will handle the routing and immediately drop our leg of the call.
            # This automatically triggers the normal CALL_CLOSED cleanup sequence.
        else:
            print("[MediaEngine] Cannot transfer: No active call.")

    def make_outbound_call(self, target_extension):
        if self.active_call is not None:
            print("[MediaEngine] Line busy. Aborting outbound dialing sequence.")
            return None
        
        # Create a temporary tracking ID
        generated_id = f"out-{int(time.time())}"
        self.active_call_id = generated_id
        self.media_active = False
        
        # FIX: Pass the target_extension twice to satisfy the new Name/Number signature
        self.active_call = BaresipCallInstance(target_extension, target_extension, generated_id, self)
        
        print(f"[MediaEngine] Spawning outbound track path to extension: {target_extension}")
        self._send_cmd("dial", str(target_extension))
        return self.active_call

    def engage_outbound_media(self):
        while not self.pbx_to_ai_queue.empty():
            try:
                self.pbx_to_ai_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        with self.tx_lock: self.tx_buffer.clear()
        self._is_running = True
        self.media_active = True
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()
        return True

    def answer_call(self, call):
        success = self._send_cmd("accept")
        if not success:
            print("[MediaEngine] Aborting audio bridge: Baresip failed to accept the call.")
            return False

        while not self.pbx_to_ai_queue.empty():
            try:
                self.pbx_to_ai_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        with self.tx_lock: self.tx_buffer.clear()
        self._is_running = True
        self.media_active = True
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()
        return True

    def drop_call(self):
        # self.media_active = False
        if self.active_call != None:
            self._send_cmd("hangup")
        self.active_call = None
        self.active_call_id = None
    
    def flush_tx_buffer(self):
        with self.tx_lock:
            self.tx_buffer.clear()

    def inject_audio(self, pcm_8k_bytes):
        with self.tx_lock:
            self.tx_buffer.extend(pcm_8k_bytes)

    def _heartbeat_loop(self):
        print("[MediaEngine] Software Audio Bridge Engaged (Callback Mode).")
        
        def audio_callback(indata, outdata, frames, time_info, status):
            # --- RX PHASE: PulseAudio -> AI ---
            try:
                self.main_loop.call_soon_threadsafe(self.pbx_to_ai_queue.put_nowait, bytes(indata))
            except queue.Full:
                pass

            # --- TX PHASE: AI -> PulseAudio ---
            required_bytes = frames * 2  # 16-bit Mono = 2 bytes per frame
            with self.tx_lock:
                if len(self.tx_buffer) >= required_bytes:
                    # Inject AI audio into the outbound stream
                    outdata[:] = self.tx_buffer[:required_bytes]
                    del self.tx_buffer[:required_bytes]
                else:
                    # Inject silence if the AI buffer is empty
                    outdata[:] = b'\x00' * required_bytes

        try:
            # Initialize the stream with the background callback
            with sd.RawStream(
                samplerate=8000, 
                blocksize=160, 
                channels=1, 
                dtype='int16',
                callback=audio_callback,
                latency='low'
            ):
                # The while loop now only exists to keep the thread alive. 
                # It does zero audio processing.
                while self._is_running and self.active_call and self.media_active:
                    time.sleep(0.1)
                    
        except Exception as e:
            print(f"[MediaEngine] Failed to open callback stream: {e}")
            
        print("\n[MediaEngine] Audio Bridge stopped.")