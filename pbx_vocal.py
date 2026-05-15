import threading
import queue
from pyVoIP.VoIP import VoIPPhone, CallState, InvalidStateError
import numpy as np

class MediaEngine:
    def __init__(self, sip_ip, sip_port, username, password, on_call_callback, my_ip):
        self.username = username
        self.on_call_callback = on_call_callback
        
        self.phone = VoIPPhone(
            sip_ip, sip_port, username, password, 
            callCallback=self._internal_incoming_call_handler,
            myIP=my_ip
        )
        
        self.active_call = None
        self._is_running = False
        self.heartbeat_thread = None
        
        # Audio transport queues
        self.pbx_to_ai_queue = queue.Queue() 
        self.tx_buffer = bytearray()
        self.tx_lock = threading.Lock()

    def start(self):
        self.phone.start()
        print(f"[MediaEngine] Registered SIP account: {self.username}")

    def stop(self):
        self._is_running = False
        if self.active_call:
            try:
                self.active_call.hangup()
            except InvalidStateError:
                pass
        self.phone.stop()
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=2.0)

    def _internal_incoming_call_handler(self, call):
        """Rejects secondary calls if an active call is already in progress."""
        if self.active_call is not None:
            caller = call.request.headers.get('From', {}).get('caller', 'Unknown')
            print(f"[MediaEngine] Line busy. Automatically rejecting secondary call from {caller}.")
            try:
                call.deny()
            except AttributeError:
                # Suppress pyVoIP's internal RTP bug where it tries to close an uninitialized socket
                pass
            except Exception as e:
                print(f"[MediaEngine] Minor error while rejecting call: {e}")
            return
            
        self.on_call_callback(call)

    def make_outbound_call(self, target_extension):
        """Initiates an outbound SIP invite."""
        if self.active_call is not None:
            print("[MediaEngine] Cannot dial out, line is busy.")
            return None
        
        try:
            # pyVoIP's native outbound dialing method
            outbound_call = self.phone.call(target_extension)
            self.active_call = outbound_call
            return outbound_call
        except Exception as e:
            print(f"[MediaEngine] Failed to initiate outbound call: {e}")
            return None

    def engage_outbound_media(self):
        """Starts the RTP heartbeat for an answered outbound call."""
        if not self.active_call or self.active_call.state.name != "ANSWERED":
            return False
            
        print(f"[MediaEngine] Outbound call connected. Engaging RTP Sync.")
        with self.pbx_to_ai_queue.mutex: self.pbx_to_ai_queue.queue.clear()
        with self.tx_lock: self.tx_buffer.clear()
        
        self._is_running = True
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()
        return True

    def answer_call(self, call):
        """Answers the call and starts the synchronized RTP heartbeat loop."""
        self.active_call = call
        try:
            self.active_call.answer()
            print(f"[MediaEngine] Answered call from {call.request.headers['From']['caller']}")
            
            # Flush state from previous calls
            with self.pbx_to_ai_queue.mutex: self.pbx_to_ai_queue.queue.clear()
            with self.tx_lock: self.tx_buffer.clear()
            
            self._is_running = True
            self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            self.heartbeat_thread.start()
        except InvalidStateError:
            print("[MediaEngine] Call dropped before we could answer.")

    def drop_call(self):
        self._is_running = False
        if self.active_call:
            try:
                self.active_call.hangup()
            except InvalidStateError:
                pass
        self.active_call = None
    
    def flush_tx_buffer(self):
        """Instantly clears the transmit buffer when the AI is interrupted."""
        with self.tx_lock:
            self.tx_buffer.clear()
            print("[MediaEngine] TX Buffer Flushed (AI Interrupted).")

    def inject_audio(self, pcm_8k_bytes):
        """Thread-safe injection of audio payloads from the AI client into the PBX transmit buffer."""
        with self.tx_lock:
            self.tx_buffer.extend(pcm_8k_bytes)

    def _heartbeat_loop(self):
        """Synchronous 20ms RTP loop handling bidirectional audio transport."""
        print("[MediaEngine] PBX RTP Sync engaged (Linear PCM Mode).")
        
        while self._is_running and self.active_call and self.active_call.state == CallState.ANSWERED:
            
            # --- RX PHASE: PBX to AI ---
            try:
                # pyVoIP outputs raw Unsigned 8-bit PCM
                rx_raw_unsigned_8 = self.active_call.read_audio(length=160, blocking=True)
                if not rx_raw_unsigned_8:
                    break
                
                # Map pyVoIP Unsigned 8-bit to Standard Signed 16-bit PCM
                rx_arr = np.frombuffer(rx_raw_unsigned_8, dtype=np.uint8)
                rx_signed_8 = rx_arr.astype(np.int16) - 128
                rx_pcm_16 = (rx_signed_8 * 256).astype(np.int16).tobytes()
                
                try:
                    self.pbx_to_ai_queue.put_nowait(rx_pcm_16)
                except queue.Full:
                    pass 
            except Exception as e:
                print(f"[MediaEngine] CRITICAL RX EXCEPTION: {repr(e)}")
                break

            # --- TX PHASE: AI to PBX ---
            with self.tx_lock:
                # Extract exactly one 20ms frame (320 bytes at 8kHz 16-bit)
                if len(self.tx_buffer) >= 320:
                    tx_pcm_16 = bytes(self.tx_buffer[:320])
                    del self.tx_buffer[:320]
                else:
                    # Inject silence if AI is processing to keep RTP stream alive
                    tx_pcm_16 = b'\x00' * 320 
            
            # Map Standard Signed 16-bit PCM back to pyVoIP Unsigned 8-bit
            tx_arr_16 = np.frombuffer(tx_pcm_16, dtype=np.int16)
            tx_unsigned_8 = ((tx_arr_16 // 256) + 128).astype(np.uint8).tobytes()
            
            # CRITICAL FIX: Check if the call is still alive before writing
            if self._is_running and self.active_call:
                self.active_call.write_audio(tx_unsigned_8)
                
        print("\n[MediaEngine] PBX RTP Sync stopped.")
        self._is_running = False