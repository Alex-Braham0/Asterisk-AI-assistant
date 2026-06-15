import asyncio
import websockets
import json
import base64
import queue
import datetime
from ai.audio_processor import AudioProcessor

class GeminiSocket:
    def __init__(self, api_key, pbx_to_ai_queue, pbx_inject_callback, pbx_flush_callback, tool_handler_callback, system_instruction, tools):
        self.uri = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={api_key}"
        self.pbx_to_ai_queue = pbx_to_ai_queue
        self.pbx_inject_callback = pbx_inject_callback
        self.pbx_flush_callback = pbx_flush_callback
        self.tool_handler_callback = tool_handler_callback
        self.system_instruction = system_instruction
        self.tools = tools
        self.ws = None
        self.is_connected = False
        self.summary_requested = False
        self.tool_call_pending = False
        self.ai_speaking_event = asyncio.Event()

    def _log(self, msg):
        timestamp = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        print(f"[{timestamp}] {msg}")

    async def connect(self):
        """Configures the Live API for bidirectional audio."""
        self._log("[Gemini] Initiating WS Connection...")
        self.ws = await websockets.connect(self.uri)
        
        setup_message = {
            "setup": {
                "model": "models/gemini-2.5-flash-native-audio-preview-12-2025",
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
                    "parts": [{"text": self.system_instruction}]
                },
                "tools": [{
                    "functionDeclarations": self.tools
                }]
            }
        }
        await self.ws.send(json.dumps(setup_message))
        
        response = await self.ws.recv()
        data = json.loads(response)
        
        if "setupComplete" in data:
            self.is_connected = True
            self._log("[Gemini] Handshake OK. Audio Modality Active.")
            return True
        return False

    async def run_audio_bridge(self, on_disconnect_callback):
        """Runs the concurrent Uplink (Mic) and Downlink (Speaker) tasks."""
        uplink_task = asyncio.create_task(self._uplink_loop())
        downlink_task = asyncio.create_task(self._downlink_loop())
        
        try:
            await asyncio.gather(uplink_task, downlink_task)
        except asyncio.CancelledError:
            pass
        finally:
            self.is_connected = False
            if self.ws:
                await self.ws.close()
            if not self.summary_requested:
                self._log("[Gemini] Connection lost abruptly. Triggering SIP hangup.")
                on_disconnect_callback()

    async def _uplink_loop(self):
        """Batches 8kHz PCM frames from the PBX and sends them to the Gemini API."""
        audio_buffer = bytearray()
        
        while self.is_connected:
            if self.tool_call_pending:
                await asyncio.sleep(0.01)
                continue

            try:
                pcm_8k = await self.pbx_to_ai_queue.get() 
                audio_buffer.extend(pcm_8k)
                
                if len(audio_buffer) >= 1600:
                    chunk_8k = bytes(audio_buffer[:1600])
                    del audio_buffer[:1600]
                    
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
                    
            except queue.Empty:
                await asyncio.sleep(0.01)
            except Exception as e:
                self._log(f"[Gemini Uplink Error] {e}")
                break

    async def _downlink_loop(self):
        """Receives 24kHz audio and tool calls from Gemini."""
        buffer_24k = bytearray()
        
        while self.is_connected:
            try:
                response = await self.ws.recv()
                data = json.loads(response)
                
                if "serverContent" not in data or "modelTurn" not in data.get("serverContent", {}):
                    self._log(f"[RAW WS DEBUG] {json.dumps(data)}")
                
                # 1. Handle AUDIO and TEXT
                if "serverContent" in data:
                    content = data["serverContent"]

                    if content.get("turnComplete"):
                        self.ai_speaking_event.clear()
                    
                    if content.get("interrupted"):
                        self._log("[Gemini] User Interrupted. Flushing audio buffer.")
                        self.pbx_flush_callback()
                        
                    if "modelTurn" in content:
                        parts = content["modelTurn"].get("parts", [])
                        for part in parts:
                            if "text" in part:
                                self._log(f"[Gemini] Transcript: '{part['text'].strip()}'")
                            
                            if "inlineData" in part:
                                mime_type = part["inlineData"].get("mimeType", "")
                                if mime_type.startswith("audio/pcm"):
                                    b64_audio = part["inlineData"]["data"]
                                    pcm_api = base64.b64decode(b64_audio)
                                    buffer_24k.extend(pcm_api)
                                    
                    # Delegate downsampling to the AudioProcessor
                    audio_8k, buffer_24k = AudioProcessor.process_downlink_audio(buffer_24k)
                    if audio_8k:
                        self.pbx_inject_callback(audio_8k)
                        
                # 2. Handle TOOL CALLS
                if "toolCall" in data:
                    self.tool_call_pending = True
                    self._log("[Gemini] Tool Call received. Pausing audio uplink.")
                    function_calls = data["toolCall"].get("functionCalls", [])
                    for call in function_calls:
                        call_id = call.get("id")
                        func_name = call.get("name")
                        func_args = call.get("args", {})
                        
                        asyncio.create_task(self.tool_handler_callback(call_id, func_name, func_args))
                        
            except websockets.exceptions.ConnectionClosed:
                self._log("[Gemini] WebSocket Closed by Server.")
                break
            except Exception as e:
                self._log(f"[Gemini Downlink Error] {e}")
                break

    async def send_tool_response(self, call_id, name, result_data):
        """Packages the execution result and sends it back to the AI."""
        response_msg = {
            "toolResponse": {
                "functionResponses": [{
                    "id": call_id,
                    "name": name,
                    "response": result_data
                }]
            }
        }
        try:
            await self.ws.send(json.dumps(response_msg))
            self._log(f"[Gemini] Sent toolResponse for {name}.")
            self.tool_call_pending = False
        except Exception as e:
            self._log(f"[Gemini] Failed to send toolResponse: {e}")
            self.tool_call_pending = False 

    async def request_summary_and_close(self, reason):
        """Injects the command to execute the summary tool (STRICT NON-VERBAL ENFORCEMENT)."""
        if self.summary_requested:
            return
        
        self.summary_requested = True
        self._log(f"[Gemini] Requesting final summary. Reason: {reason}")
        
        self.pbx_flush_callback()
        self.pbx_inject_callback = lambda x: None 
        
        command_text = (
            f"SYSTEM COMMAND: {reason} "
            "You are required to execute the 'submit_call_summary' tool immediately. "
            "CRITICAL DIRECTIVE: DO NOT output any text, thoughts, or speech. "
            "You MUST output ONLY the JSON payload for the `submit_call_summary` tool immediately."
        )
        
        msg = {
            "clientContent": {
                "turns": [{"role": "user", "parts": [{"text": command_text}]}],
                "turnComplete": True
            }
        }
        
        try:
            await self.ws.send(json.dumps(msg))
        except Exception as e:
            self._log(f"[Gemini] Failed to send summary request: {e}")
            self.is_connected = False

    async def send_system_event(self, text_event):
        """Injects a text command into the live audio stream to guide AI behavior."""
        msg = {
            "clientContent": {
                "turns": [{"role": "user", "parts": [{"text": f"SYSTEM EVENT: {text_event}"}]}],
                "turnComplete": True
            }
        }
        try:
            await self.ws.send(json.dumps(msg))
            self._log(f"[Gemini] Injected System Event: {text_event}")
        except Exception as e:
            self._log(f"[Gemini] Failed to send System Event: {e}")