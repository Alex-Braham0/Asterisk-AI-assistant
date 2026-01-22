import socket, struct, time, threading, json, asyncio, base64, websockets, traceback, requests, audioop, ctypes, wave, collections
from contextlib import suppress

# --- MODULE 1: CONFIGURATION ---
class Config:
    def __init__(self, pbx_ip, my_ip, keys):
        self.pbx_ip = pbx_ip
        self.my_ip = my_ip
        self.auth = ("alexpc", keys.get('freepbx-pass'))
        self.gemini_key = keys.get('gemini-key')
        self.app_name = "my_audio_app"
        self.model = "models/gemini-2.5-flash-native-audio-preview-12-2025"
        self.base_url = f"http://{pbx_ip}:8088/ari"
        self.default_prompt = "You are a helpful phone assistant. Keep responses concise."

# --- MODULE 2: AUDIO ENGINE ---
class AudioEngine:
    def __init__(self):
        self.in_state = None
        self.out_state = None
        self.to_gemini = asyncio.Queue()  
        self.from_gemini = collections.deque()
        self.lock = threading.Lock()
        self.is_speaking = False
        self.last_interrupt_t = 0

    def process_inbound(self, data_8k_be, loop):
        le_8k = audioop.byteswap(data_8k_be, 2)
        resampled, self.in_state = audioop.ratecv(le_8k, 2, 1, 8000, 16000, self.in_state)
        loop.call_soon_threadsafe(self.to_gemini.put_nowait, resampled)

    def process_outbound(self, data_24k_le):
        resampled_le, self.out_state = audioop.ratecv(data_24k_le, 2, 1, 24000, 8000, self.out_state)
        return audioop.byteswap(resampled_le, 2)

    def clear(self):
        with self.lock:
            self.from_gemini.clear()
            self.is_speaking = False
            while not self.to_gemini.empty():
                with suppress(asyncio.QueueEmpty): self.to_gemini.get_nowait()

# --- MODULE 3: GEMINI INTERFACE ---
class GeminiInterface:
    def __init__(self, config, engine, system_instruction=None):
        self.cfg = config
        self.engine = engine
        self.uri = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={config.gemini_key}"
        self.ready_event = asyncio.Event()
        self.system_instruction = system_instruction or config.default_prompt

    async def run(self, ws):
        setup = {
            "setup": {
                "model": self.cfg.model,
                "system_instruction": {"parts": [{"text": self.system_instruction}]},
                "generation_config": {"response_modalities": ["audio"]}
            }
        }
        await ws.send(json.dumps(setup))
        await asyncio.gather(self._send_loop(ws), self._receive_loop(ws))

    async def _send_loop(self, ws):
        while True:
            data = await self.engine.to_gemini.get()
            msg = {"realtime_input": {"media_chunks": [{"data": base64.b64encode(data).decode('utf-8'), "mime_type": "audio/pcm;rate=16000"}]}}
            await ws.send(json.dumps(msg))

    async def _receive_loop(self, ws):
        async for msg in ws:
            resp = json.loads(msg)
            if "setupComplete" in resp: self.ready_event.set()
            if "serverContent" in resp:
                content = resp["serverContent"]
                
                if content.get("interrupted") and self.engine.is_speaking:
                    self.engine.clear()
                    continue

                if content.get("turnComplete"):
                    self.engine.is_speaking = False

                for part in content.get("modelTurn", {}).get("parts", []):
                    data_blob = part.get("inlineData") or part.get("inline_data")
                    if data_blob:
                        self.engine.is_speaking = True
                        be_8k = self.engine.process_outbound(base64.b64decode(data_blob["data"]))
                        with self.engine.lock:
                            for i in range(0, len(be_8k), 320):
                                self.engine.from_gemini.append(be_8k[i:i+320])

# --- MODULE 4: MEDIA WORKERS ---
class MediaWorker:
    @staticmethod
    def rtp_out(pbx_ip, pbx_port, state, engine):
        seq, ts, sent, sock = 100, 160, 0, socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        start_t = None
        while state['active']:
            chunk = None
            with engine.lock:
                if engine.from_gemini: chunk = engine.from_gemini.popleft()
                elif start_t: start_t = None
            
            if chunk:
                if not start_t: start_t = time.perf_counter(); sent = 0
                header = struct.pack(">BBHII", 0x80, 11, seq, ts, 0x1234)
                sock.sendto(header + chunk, (pbx_ip, pbx_port))
                seq, ts, sent = (seq + 1) % 65536, (ts + 160) % 4294967296, sent + 1
                target = start_t + (sent * 0.02)
                wait = target - time.perf_counter()
                if wait > 0: time.sleep(wait)
            else: time.sleep(0.005)

    @staticmethod
    def rtp_in(port, state, engine, loop):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('0.0.0.0', port))
        while state['active']:
            try:
                sock.settimeout(1.0)
                data, _ = sock.recvfrom(2048)
                if len(data) > 12: engine.process_inbound(data[12:], loop)
            except: continue
        sock.close()

# --- MODULE 5: GATEWAY CORE ---
class GeminiGateway:
    def __init__(self, config):
        self.cfg = config
        self.call_lock = asyncio.Lock()
        self.state = {'active': False, 'chan_id': None}

    async def handle_call(self, c_id, c_name, caller_num, loop):
        if self.call_lock.locked():
            print(f"[!] REJECTED: Busy with another call."); requests.delete(f"{self.cfg.base_url}/channels/{c_id}", auth=self.cfg.auth); return
        
        async with self.call_lock:
            print(f"\n[1] CALL STARTED: {c_name} (From: {caller_num})")
            self.state.update({'active': True, 'chan_id': c_id})
            engine = AudioEngine()
            ai = GeminiInterface(self.cfg, engine)
            
            m_out_id, m_in_id, snoop_id = None, None, None
            
            try:
                async with websockets.connect(ai.uri) as ws:
                    asyncio.create_task(ai.run(ws))
                    await asyncio.wait_for(ai.ready_event.wait(), 15)
                    print("[2] READY. Answering.")
                    requests.post(f"{self.cfg.base_url}/channels/{c_id}/answer", auth=self.cfg.auth)
                    
                    m_out = requests.post(f"{self.cfg.base_url}/channels/externalMedia", auth=self.cfg.auth, params={"app": self.cfg.app_name, "external_host": f"{self.cfg.my_ip}:12345", "format": "slin"}).json()
                    m_in = requests.post(f"{self.cfg.base_url}/channels/externalMedia", auth=self.cfg.auth, params={"app": self.cfg.app_name, "external_host": f"{self.cfg.my_ip}:12346", "format": "slin"}).json()
                    m_out_id, m_in_id = m_out['id'], m_in['id']
                    
                    requests.post(f"{self.cfg.base_url}/bridges/b1", auth=self.cfg.auth, params={"type": "mixing"})
                    requests.post(f"{self.cfg.base_url}/bridges/b1/addChannel", auth=self.cfg.auth, params={"channel": f"{m_out_id},{c_id}"})
                    
                    snoop = requests.post(f"{self.cfg.base_url}/channels/{c_id}/snoop", auth=self.cfg.auth, params={"app": self.cfg.app_name, "spy": "in"}).json()
                    snoop_id = snoop['id']
                    requests.post(f"{self.cfg.base_url}/bridges/b2", auth=self.cfg.auth, params={"type": "mixing"})
                    requests.post(f"{self.cfg.base_url}/bridges/b2/addChannel", auth=self.cfg.auth, params={"channel": f"{snoop_id},{m_in_id}"})

                    threading.Thread(target=MediaWorker.rtp_in, args=(12346, self.state, engine, loop)).start()
                    threading.Thread(target=MediaWorker.rtp_out, args=(self.cfg.pbx_ip, int(m_out['channelvars']['UNICASTRTP_LOCAL_PORT']), self.state, engine)).start()
                    
                    while self.state['active']: await asyncio.sleep(0.5)

                if engine.is_speaking:
                    print("[*] CLEANUP: Waiting for Gemini to finish response...")
                    wait_start = time.time()
                    while engine.is_speaking and (time.time() - wait_start < 5.0):
                        await asyncio.sleep(0.1)

            except: traceback.print_exc()
            finally:
                print("[*] CLEANUP: Removing Channels...")
                engine.clear()
                for cid in [m_out_id, m_in_id, snoop_id, c_id]:
                    if cid: 
                        with suppress(Exception): requests.delete(f"{self.cfg.base_url}/channels/{cid}", auth=self.cfg.auth)
                print("[+] CALL COMPLETE.")

    def run(self):
        try:
            ctypes.WinDLL('winmm').timeBeginPeriod(1)
        except: pass
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        def on_msg(ws, msg):
            event = json.loads(msg)
            etype, c = event.get('type'), event.get('channel', {})
            c_id, c_name = c.get('id'), c.get('name', '')

            if etype == "StasisStart":
                if "PJSIP/" in c_name and not any(x in c_name for x in ["Snoop", "Unicast"]):
                    caller_num = c.get('caller', {}).get('number', 'unknown')
                    loop.call_soon_threadsafe(asyncio.create_task, self.handle_call(c_id, c_name, caller_num, loop))
            elif etype == "StasisEnd":
                if c_id == self.state.get('chan_id'):
                    print(f"--- HANGUP DETECTED: {c_name} ---")
                    self.state['active'] = False

        import websocket as ws_lib
        url = f"ws://{self.cfg.pbx_ip}:8088/ari/events?app={self.cfg.app_name}&api_key={self.cfg.auth[0]}:{self.cfg.auth[1]}"
        threading.Thread(target=ws_lib.WebSocketApp(url, on_message=on_msg).run_forever, daemon=True).start()
        print(f"[*] READY: Listening for '{self.cfg.app_name}'"); loop.run_forever()

if __name__ == "__main__":
    with open('keys.json') as f: keys = json.load(f)
    cfg = Config("192.168.1.200", "192.168.1.10", keys)
    GeminiGateway(cfg).run()