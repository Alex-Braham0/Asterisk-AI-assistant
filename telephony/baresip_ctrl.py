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
        self.udp_port = 5555  # Will be dynamically overwritten by the MediaChannel
        self._is_running = False
        self.listener_thread = None

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

        max_retries = 3
        for attempt in range(max_retries):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1.0)
                    s.connect((self.ctrl_host, self.ctrl_port))
                    s.sendall(netstring_payload.encode('utf-8'))
                    response = s.recv(2048)
                    if not response:
                        time.sleep(0.2)
                        continue
                    return True
            except Exception:
                time.sleep(0.2)
                
        print(f"[BaresipCtrl] CRITICAL: Failed to send command '{command}' after {max_retries} attempts.")
        return False

    def send_dtmf_udp(self, digit: str):
        clean_digit = str(digit).strip()[0]
        try:
            udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Use the dynamic UDP port tied to this specific channel
            udp_sock.sendto(clean_digit.encode('utf-8'), ("127.0.0.1", self.udp_port))
            udp_sock.close()
        except Exception as e:
            print(f"[BaresipCtrl] Failed to send UDP DTMF: {e}")

    def _listener_loop(self):
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
                                self.event_callback(event)
                        except json.JSONDecodeError: continue
                s.close()
            except Exception as e:
                print(f"\n[BaresipCtrl] Connection Loop Dropped: {e}")
                time.sleep(1)