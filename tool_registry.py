import json
import asyncio

class ToolRegistry:
    def __init__(self, session):
        """
        Holds a reference to the active CallSession so tools can manipulate 
        the call state (like hanging up) without knowing about the WebSocket.
        """
        self.session = session

    def get_declarations(self):
        """Returns the JSON schema for all available tools."""
        return [
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

    async def execute_tool(self, name, args):
        """Executes the Python logic for a requested tool."""
        
        if name == "end_call":
            print("[ToolRegistry] AI initiated call termination. Dropping SIP line.")
            self.session.drop_call()
            
            # Trigger the summary request asynchronously
            asyncio.create_task(self.session.trigger_summary(
                reason="You (the AI) have ended the call via the 'end_call' tool."
            ))
            
            return {"status": "success", "message": "SIP line disconnected. Awaiting summary execution."}
            
        elif name == "submit_call_summary":
            print(f"\n--- Final Call Summary (JSON) ---\n{json.dumps(args, indent=2)}\n---------------------------------")
            
            # Force a SIP drop here just in case the AI bypassed the 'end_call' tool
            self.session.drop_call()
            
            # Terminate the AI Bridge immediately. 
            self.session.terminate_bridge()
            
            # Return None to signal to the GeminiClient that the connection is dead 
            # and no toolResponse should be sent.
            return None
            
        return {"error": "Function not implemented."}