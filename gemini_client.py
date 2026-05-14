import asyncio
import websockets
import json
import base64
import queue
import datetime
import numpy as np

class GeminiClient:
    def __init__(self, api_key, pbx_to_ai_queue, pbx_inject_callback, pbx_flush_callback, pbx_drop_call_callback, system_instruction):
        self.uri = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={api_key}"
        self.pbx_to_ai_queue = pbx_to_ai_queue
        self.pbx_inject_callback = pbx_inject_callback
        self.pbx_flush_callback = pbx_flush_callback
        self.pbx_drop_call_callback = pbx_drop_call_callback
        self.system_instruction = system_instruction
        self.ws = None
        self.is_connected = False
        self.down_state = None
        self.summary_requested = False

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
                    "responseModalities": ["AUDIO"]
                },
                "systemInstruction": {
                    "parts": [{"text": self.system_instruction}]
                },
                "tools": [{
                    "functionDeclarations": [
                        {
                            "name": "end_call",
                            "description": "Hangs up the phone call. Execute this immediately when the user says goodbye, asks to end the call, or the conversation naturally concludes.",
                            "parameters": {
                                "type": "OBJECT",
                                "properties": {},
                                "required": []
                            }
                        },
                        {
                            "name": "submit_call_summary",
                            "description": "DO NOT USE THIS TOOL MANUALLY. Only execute this tool when the system explicitly commands you to via a SYSTEM COMMAND. Do not use this when the user asks to hang up.",
                            "parameters": {
                                "type": "OBJECT",
                                "properties": {
                                    "key_topics": {"type": "ARRAY", "items": {"type": "STRING"}},
                                    "action_items": {"type": "ARRAY", "items": {"type": "STRING"}},
                                    "user_sentiment": {"type": "STRING"}
                                },
                                "required": ["key_topics", "action_items"]
                            }
                        }
                    ]
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

    async def run_audio_bridge(self):
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

    async def _uplink_loop(self):
        """Batches 8kHz PCM frames from the PBX and sends them to the Gemini API."""
        audio_buffer = bytearray()
        
        while self.is_connected:
            try:
                pcm_8k = self.pbx_to_ai_queue.get_nowait()
                audio_buffer.extend(pcm_8k)
                
                # Buffer to 100ms chunks to optimize WebSocket network traffic
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
                
                # --- Debug Logging ---
                if "serverContent" not in data or "modelTurn" not in data.get("serverContent", {}):
                    self._log(f"[RAW WS DEBUG] {json.dumps(data)}")
                
                # ==========================================
                # 1. Handle AUDIO and TEXT (serverContent block)
                # ==========================================
                if "serverContent" in data:
                    content = data["serverContent"]
                    
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
                                    
                    # Downsample logic
                    while len(buffer_24k) >= 9600:
                        large_chunk_24k = bytes(buffer_24k[:9600])
                        del buffer_24k[:9600]
                        
                        audio_24k = np.frombuffer(large_chunk_24k, dtype=np.int16)
                        audio_24k = np.clip(audio_24k * 0.8, -32768, 32767).astype(np.int16)
                        audio_8k = audio_24k[::3]
                        self.pbx_inject_callback(audio_8k.tobytes())
                        
                # ==========================================
                # 2. Handle TOOL CALLS (Root Level block)
                # ==========================================
                if "toolCall" in data:
                    function_calls = data["toolCall"].get("functionCalls", [])
                    for call in function_calls:
                        func_name = call.get("name")
                        func_args = call.get("args", {})
                        call_id = call.get("id")
                        
                        # --- Intercept the Summary Tool ---
                        if func_name == "submit_call_summary":
                            self._log(f"\n--- Final Call Summary (JSON) ---\n{json.dumps(func_args, indent=2)}\n---------------------------------")
                            
                            # CRITICAL: Force a SIP drop here just in case the AI bypassed the 'end_call' tool!
                            try:
                                self.pbx_drop_call_callback()
                            except Exception:
                                pass

                            # Terminate the AI Bridge immediately.
                            self.is_connected = False
                            break
                        # ----------------------------------
                        
                        self._log(f"[Gemini] Tool Execution Requested: {func_name}({func_args})")
                        
                        # Execute local logic for standard tools
                        result_data = self._route_tool_execution(func_name, func_args)
                        
                        # Send the result back to Gemini immediately
                        asyncio.create_task(self._send_tool_response(call_id, func_name, result_data))
                        
            except websockets.exceptions.ConnectionClosed:
                self._log("[Gemini] WebSocket Closed by Server.")
                break
            except Exception as e:
                self._log(f"[Gemini Downlink Error] {e}")
                break

    def _route_tool_execution(self, name, args):
        """Routes the requested function to local Python logic."""
        if name == "end_call":
            self._log("[Gemini] AI initiated call termination. Dropping SIP line.")
            # Trigger the PBX to send the SIP BYE packet
            self.pbx_drop_call_callback()
            
            # Safely schedule the summary request in the event loop
            asyncio.create_task(self.request_summary_and_close(
                reason="You (the AI) have ended the call via the 'end_call' tool."
            ))
            
            return {"status": "success", "message": "Call disconnected successfully."}
        
        return {"error": "Function not implemented."}

    async def _send_tool_response(self, call_id, name, result_data):
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
        except Exception as e:
            self._log(f"[Gemini] Failed to send toolResponse: {e}")

    async def request_summary_and_close(self, reason):
        """Forces the AI to execute the summary tool before closing the connection."""
        if self.summary_requested:
            return
        
        self.summary_requested = True
        self._log(f"[Gemini] Requesting final summary. Reason: {reason}")
        
        # 1. Silence the PBX buffer immediately and stop downlink injection
        self.pbx_flush_callback()
        self.pbx_inject_callback = lambda x: None 
        
        # 2. Inject the modular system command
        command_text = (
            f"SYSTEM COMMAND: {reason} "
            "You are required to execute the 'submit_call_summary' tool immediately. "
            "CRITICAL: Do not speak. Do not narrate your actions. Do not output any text or thoughts. "
            "Execute the function immediately."
        )
        
        msg = {
            "clientContent": {
                "turns": [{
                    "role": "user",
                    "parts": [{"text": command_text}]
                }],
                "turnComplete": True
            }
        }
        
        try:
            await self.ws.send(json.dumps(msg))
            # We do NOT set is_connected = False here. 
            # We must wait for the _downlink_loop to catch the tool payload.
        except Exception as e:
            self._log(f"[Gemini] Failed to send summary request: {e}")
            self.is_connected = False