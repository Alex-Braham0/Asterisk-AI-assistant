import socket
import json
import time
import threading

class MockSIPRequest:
    def __init__(self, caller_name, caller_number):
        self.headers = {
            'From': {'caller': str(caller_name), 'number': str(caller_number).strip()}
        }

class BaresipCallInstance:
    def __init__(self, caller_name, caller_number, call_id):
        self._id = call_id
        self.request = MockSIPRequest(caller_name, caller_number)
        
        import asyncio
        self.answered_event = asyncio.Event()
        self.ended_event = asyncio.Event()

    def deny(self):
        pass

class BaresipController:
    def __init__(self, ctrl_host: str, ctrl_port: int, event_callback):
        self.ctrl_host = ctrl_host
        self.ctrl_port = ctrl_port
        self.event_callback = event_callback
        self.udp_port = 5555  
        self._is_running = False
        self.listener_thread = None
        
        # New: Track the active socket so we can reuse it
        self.active_socket = None
        self.socket_lock = threading.Lock()

    def start(self):
        self._is_running = True
        self.listener_thread = threading.Thread(target=self._listener_loop, daemon=True)
        self.listener_thread.start()

    def stop(self):
        self._is_running = False
        if self.listener_thread:
            self.listener_thread.join(timeout=2.0)

    def send_cmd(self, command: str, params: str = "") -> bool:
        payload = {"command": command.strip().replace('/', ''), "params": params.strip()}
        json_payload = json.dumps(payload)
        netstring_payload = f"{len(json_payload)}:{json_payload},"

        # Send the command down the existing listener socket instantly
        with self.socket_lock:
            if not self.active_socket:
                print(f"[BaresipCtrl] Cannot send '{command}', socket is not connected.")
                return False
            try:
                self.active_socket.sendall(netstring_payload.encode('utf-8'))
                return True
            except Exception as e:
                print(f"[BaresipCtrl] Socket error during '{command}': {e}")
                return False

    def send_dtmf_udp(self, digit: str):
        clean_digit = str(digit).strip()[0]
        try:
            udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_sock.sendto(clean_digit.encode('utf-8'), ("127.0.0.1", self.udp_port))
            udp_sock.close()
        except Exception as e:
            print(f"[BaresipCtrl] Failed to send UDP DTMF: {e}")

    def _safe_dispatch(self, event):
        try:
            self.event_callback(event)
        except Exception as e:
            print(f"[BaresipCtrl] Error during event callback execution: {e}")

    def _listener_loop(self):
        while self._is_running:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((self.ctrl_host, self.ctrl_port))
                s.settimeout(1.0)
                
                # Register the socket as active
                with self.socket_lock:
                    self.active_socket = s
                    
                print(f"[BaresipCtrl] Successfully connected to Baresip at {self.ctrl_host}:{self.ctrl_port}")
                
                buffer = ""
                while self._is_running:
                    try:
                        data = s.recv(4096).decode('utf-8', errors='ignore')
                    except socket.timeout:
                        continue 
                        
                    if not data: 
                        print("[BaresipCtrl] Connection closed by Baresip.")
                        break 
                    
                    buffer += data
                    while True:
                        if not buffer or ":" not in buffer: 
                            break
                        try:
                            len_str, remaining = buffer.split(":", 1)
                            length = int(len_str)
                        except ValueError:
                            next_colon = buffer.find(':', 1)
                            if next_colon != -1:
                                buffer = buffer[next_colon - 1:]
                            else:
                                buffer = ""
                            continue
                            
                        if len(remaining) < (length + 1): 
                            break
                            
                        json_payload = remaining[:length]
                        buffer = remaining[length + 1:] 
                        
                        try:
                            event = json.loads(json_payload)
                            if isinstance(event, dict):
                                threading.Thread(target=self._safe_dispatch, args=(event,), daemon=True).start()
                        except json.JSONDecodeError: 
                            continue
                
                # Deregister the socket if the connection drops
                with self.socket_lock:
                    self.active_socket = None        
                s.close()
                time.sleep(1) 
                
            except Exception as e:
                with self.socket_lock:
                    self.active_socket = None
                if "Connection refused" not in str(e):
                    print(f"[BaresipCtrl] Socket Error on {self.ctrl_port}: {e}")
                time.sleep(1)