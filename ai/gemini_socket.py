import asyncio
import websockets
import json
import base64
import numpy as np

class GeminiSocket:
    def __init__(self, api_key: str, system_prompt: str, pbx_to_ai_queue: asyncio.Queue, pbx_inject_callback, pbx_flush_callback, model: str = "models/gemini-2.5-flash-native-audio-preview-12-2025"):
        self.uri = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={api_key}"
        self.system_prompt = system_prompt
        self.pbx_to_ai_queue = pbx_to_ai_queue
        self.pbx_inject_callback = pbx_inject_callback
        self.pbx_flush_callback = pbx_flush_callback
        self.model = model
        self.ws = None
        self.is_connected = False
        self.turn_complete_event = asyncio.Event()

    async def connect(self) -> bool:
        print("[Gemini] Opening WebSocket connection...")
        try:
            self.ws = await websockets.connect(self.uri)
            
            setup_message = {
                "setup": {
                    "model": self.model,
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "speechConfig": {
                            "voiceConfig": {
                                "prebuiltVoiceConfig": {
                                    "voiceName": "Puck"
                                }
                            }
                        }
                    },
                    "systemInstruction": {
                        "parts": [{"text": self.system_prompt}]
                    }
                }
            }
            await self.ws.send(json.dumps(setup_message))
            
            response = await self.ws.recv()
            data = json.loads(response)
            
            if "setupComplete" in data:
                self.is_connected = True
                print("[Gemini] Setup Complete. Audio bridge ready.")
                return True
            return False
        except Exception as e:
            print(f"[Gemini] Connection failed: {e}")
            return False

    async def run_audio_bridge(self, on_disconnect_callback):
        uplink_task = asyncio.create_task(self._uplink_loop(), name="uplink_task")
        downlink_task = asyncio.create_task(self._downlink_loop(), name="downlink_task")
        
        try:
            done, pending = await asyncio.wait(
                [uplink_task, downlink_task], 
                return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
        except asyncio.CancelledError:
            uplink_task.cancel()
            downlink_task.cancel()
        finally:
            self.is_connected = False
            if self.ws:
                await self.ws.close()
            on_disconnect_callback()

    async def _uplink_loop(self):
        """Reads 8kHz audio from PBX, packs into 50ms frames, sends to Gemini."""
        audio_buffer = bytearray()
        
        while self.is_connected:
            try:
                pcm_8k = await self.pbx_to_ai_queue.get() 
                audio_buffer.extend(pcm_8k)
                
                while len(audio_buffer) >= 800:
                    chunk_8k = bytes(audio_buffer[:800])
                    del audio_buffer[:800]
                    
                    b64_audio = base64.b64encode(chunk_8k).decode("utf-8")
                    msg = {
                        "realtimeInput": {
                            "mediaChunks": [{
                                "mimeType": "audio/pcm;rate=8000", 
                                "data": b64_audio
                            }]
                        }
                    }
                    await self.ws.send(json.dumps(msg))
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Gemini Uplink Error] {e}")
                break

    async def _downlink_loop(self):
        """Receives 24kHz audio from Gemini, downsamples 3:1 to 8kHz, feeds Baresip."""
        buffer_24k = bytearray()
        
        while self.is_connected:
            try:
                response = await self.ws.recv()
                data = json.loads(response)
                
                if "serverContent" in data:
                    content = data["serverContent"]

                    if content.get("turnComplete"):
                        self.turn_complete_event.set()

                    if content.get("interrupted"):
                        print("\n[Gemini] 🛑 User interrupted. Flushing audio buffers.")
                        self.pbx_flush_callback()
                        buffer_24k.clear()

                    if "modelTurn" in content:
                        for part in content["modelTurn"].get("parts", []):
                            
                            # 1. Print any text transcripts or thoughts Gemini provides
                            if "text" in part:
                                text_payload = part["text"].strip()
                                if text_payload:
                                    print(f"[Gemini Thought/Transcript]: {text_payload}")

                            # 2. Extract and queue the audio
                            if "inlineData" in part:
                                mime_type = part["inlineData"].get("mimeType", "")
                                if mime_type.startswith("audio/pcm"):
                                    pcm_api = base64.b64decode(part["inlineData"]["data"])
                                    buffer_24k.extend(pcm_api)

                    # 3 samples of 16-bit (6 bytes) downsample to 1 sample (2 bytes)
                    if len(buffer_24k) >= 6:
                        chunk_size = len(buffer_24k) - (len(buffer_24k) % 6)
                        chunk_24k = bytes(buffer_24k[:chunk_size])
                        del buffer_24k[:chunk_size]

                        audio_24k = np.frombuffer(chunk_24k, dtype=np.int16)
                        # Decimation: Take every 3rd sample. Faster and prevents waveform smearing.
                        audio_8k = audio_24k[::3]
                        self.pbx_inject_callback(audio_8k.tobytes())

            except websockets.exceptions.ConnectionClosed:
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Gemini Downlink Error] {e}")
                break

    async def send_system_text(self, text: str):
        """Injects a text-based system event into the Gemini context."""
        self.turn_complete_event.clear() # Reset the event before sending
        msg = {
            "clientContent": {
                "turns": [{"role": "user", "parts": [{"text": text}]}],
                "turnComplete": True
            }
        }
        try:
            if self.is_connected and self.ws:
                await self.ws.send(json.dumps(msg))
        except Exception as e:
            print(f"[Gemini] Failed to send system text: {e}")

    async def wait_for_turn_complete(self, timeout=5.0):
        """Waits dynamically for Gemini to finish its final thought."""
        try:
            await asyncio.wait_for(self.turn_complete_event.wait(), timeout)
            print("[Gemini] AI finished final response.")
        except asyncio.TimeoutError:
            print("[Gemini] Timeout waiting for final response. Forcing closure.")