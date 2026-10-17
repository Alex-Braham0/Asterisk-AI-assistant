import asyncio
import websockets
import json
import base64
import numpy as np

# Import the tool registry (this also loads the functions)
from ai.tools.functions import registry

class GeminiSocket:
    def __init__(
        self, 
        api_key: str, 
        system_prompt: str, 
        pbx_to_ai_queue: asyncio.Queue, 
        pbx_inject_callback, 
        pbx_flush_callback, 
        call_context: dict, 
        pbx_hangup_callback, 
        on_first_audio_callback=None,
        pbx_ungate_mic_callback=None,
        pbx_transfer_callback=None,
        pbx_hold_callback=None,
        model: str = "models/gemini-2.5-flash-native-audio-preview-12-2025"
    ):
        self.uri = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={api_key}"
        self.system_prompt = system_prompt
        self.pbx_to_ai_queue = pbx_to_ai_queue
        self.pbx_inject_callback = pbx_inject_callback
        self.pbx_flush_callback = pbx_flush_callback
        self.model = model
        self.ws = None
        self.is_connected = False
        self.turn_complete_event = asyncio.Event()
        
        # --- Context & Callbacks ---
        self.call_context = call_context
        self.call_context["tool_logs"] = [] 
        self.call_context["hangup_callback"] = pbx_hangup_callback
        self.call_context["transfer_callback"] = pbx_transfer_callback
        self.call_context["hold_callback"] = pbx_hold_callback
        self.call_context["post_turn_actions"] = []
        
        # --- Answer-on-Audio Tracking ---
        self.on_first_audio_callback = on_first_audio_callback
        self._first_audio_fired = False
        self.pbx_ungate_mic_callback = pbx_ungate_mic_callback
        self._mic_ungated = False

        self.audio_uplink_active = True

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
            
            tool_declarations = registry.get_declarations()
            if tool_declarations:
                setup_message["setup"]["tools"] = tool_declarations

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
        """Reads 8kHz audio from PBX, packs into 200ms frames, sends to Gemini."""
        audio_buffer = bytearray()
        
        while self.is_connected:
            try:
                pcm_8k = await self.pbx_to_ai_queue.get() 
                audio_buffer.extend(pcm_8k)
                
                while len(audio_buffer) >= 3200:
                    chunk_8k = bytes(audio_buffer[:3200])
                    del audio_buffer[:3200]
                    
                    if self.audio_uplink_active:
                        b64_audio = base64.b64encode(chunk_8k).decode("utf-8")
                        msg = {
                            "realtimeInput": {
                                "mediaChunks": [{"mimeType": "audio/pcm;rate=8000", "data": b64_audio}]
                            }
                        }
                        await self.ws.send(json.dumps(msg))
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Gemini Uplink Error] {e}")
                break

    async def _downlink_loop(self):
        """Receives 24kHz audio and Tool Calls from Gemini."""
        buffer_24k = bytearray()
        
        while self.is_connected:
            try:
                response = await self.ws.recv()
                data = json.loads(response)
                
                if "toolCall" in data:
                    asyncio.create_task(self._handle_tool_calls(data["toolCall"]))

                if "serverContent" in data:
                    content = data["serverContent"]

                    if content.get("turnComplete"):
                        self.turn_complete_event.set()

                        if not self._mic_ungated and self.pbx_ungate_mic_callback:
                            self._mic_ungated = True
                            self.pbx_ungate_mic_callback()

                        for action in self.call_context.get("post_turn_actions", []):
                            asyncio.create_task(action())
                        self.call_context["post_turn_actions"].clear()

                    if content.get("interrupted"):
                        print("\n[Gemini] 🛑 User interrupted. Flushing audio buffers.")
                        self.pbx_flush_callback()
                        buffer_24k.clear()

                    if "modelTurn" in content:
                        for part in content["modelTurn"].get("parts", []):
                            
                            if "text" in part:
                                text_payload = part["text"].strip()
                                if text_payload:
                                    print(f"[Gemini Thought/Transcript]: {text_payload}")

                            if "inlineData" in part:
                                mime_type = part["inlineData"].get("mimeType", "")
                                if mime_type.startswith("audio/pcm"):
                                    
                                    # --- Trigger Answer on First Audio ---
                                    if not self._first_audio_fired and self.on_first_audio_callback:
                                        self._first_audio_fired = True
                                        self.on_first_audio_callback()
                                    # -------------------------------------

                                    pcm_api = base64.b64decode(part["inlineData"]["data"])
                                    buffer_24k.extend(pcm_api)

                    # Downsample 24kHz -> 8kHz
                    if len(buffer_24k) >= 6:
                        chunk_size = len(buffer_24k) - (len(buffer_24k) % 6)
                        chunk_24k = bytes(buffer_24k[:chunk_size])
                        del buffer_24k[:chunk_size]

                        audio_24k = np.frombuffer(chunk_24k, dtype=np.int16)
                        audio_8k = audio_24k[::3]
                        self.pbx_inject_callback(audio_8k.tobytes())

            except websockets.exceptions.ConnectionClosed:
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Gemini Downlink Error] {e}")
                break

    async def _handle_tool_calls(self, tool_call_data: dict):
        """Executes requested tools via the registry and logs execution cleanly."""
        function_calls = tool_call_data.get("functionCalls", [])
        responses = []

        for call in function_calls:
            fname = call.get("name")
            fargs = call.get("args", {})
            call_id = call.get("id", "")

            print(f"\n[Gemini Tool] 🛠️  Executing '{fname}' with args: {fargs}")

            # 1. Execute tool via registry
            tool_result = await registry.execute(fname, fargs, context=self.call_context)
            
            # 2. Code-Managed Logging: Check tool metadata filters
            if registry.should_log(fname):
                clean_args = registry.sanitize_args(fname, fargs)
                clean_result = registry.sanitize_result(fname, tool_result)
                
                self.call_context["tool_logs"].append({
                    "tool": fname,
                    "arguments": clean_args,
                    "result": clean_result
                })

            print(f"[Gemini Tool] 📤 Returning result: {tool_result}")
            
            responses.append({
                "id": call_id,
                "name": fname,
                "response": {"result": tool_result}
            })

        if responses:
            response_payload = {
                "toolResponse": {
                    "functionResponses": responses
                }
            }
            try:
                if self.is_connected and self.ws:
                    await self.ws.send(json.dumps(response_payload))
            except Exception as e:
                print(f"[Gemini Tool] ❌ Failed to send tool response: {e}")

    async def send_system_text(self, text: str):
        self.turn_complete_event.clear() 
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
        try:
            await asyncio.wait_for(self.turn_complete_event.wait(), timeout)
            print("[Gemini] AI finished final response.")
        except asyncio.TimeoutError:
            print("[Gemini] Timeout waiting for final response. Forcing closure.")