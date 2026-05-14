import json
import asyncio
import aiohttp

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
            },
            {
                "name": "route_to_room",
                "description": "Look up the SIP extension for a specific physical room (e.g., Kitchen, Living Room).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "room_name": {"type": "STRING", "description": "The name of the room."}
                    },
                    "required": ["room_name"]
                }
            },
            {
                "name": "route_to_person",
                "description": "Look up the SIP extension for a specific person based on their primary assigned room.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "person_name": {"type": "STRING", "description": "The name of the person."}
                    },
                    "required": ["person_name"]
                }
            },
            {
                "name": "schedule_outbound_call",
                "description": "Schedule a future outbound call to a specific SIP extension. Use this when a user asks you to call them back or call someone else later.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "target_extension": {
                            "type": "STRING", 
                            "description": "The exact SIP extension number to dial."
                        },
                        "scheduled_time": {
                            "type": "STRING", 
                            "description": "The exact date and time to initiate the call in 'YYYY-MM-DD HH:MM:SS' format."
                        },
                        "context": {
                            "type": "STRING", 
                            "description": "The specific reason for the call. This will be read back to you when the call connects so you know why you are calling."
                        }
                    },
                    "required": ["target_extension", "scheduled_time", "context"]
                }
            },
            {
                "name": "check_weather",
                "description": "Check the weather forecast for a specific time and location to advise the user on attire.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "location": {"type": "STRING", "description": "The city or neighborhood."},
                        "time_context": {"type": "STRING", "description": "e.g., 'tonight', 'tomorrow morning', 'now'"}
                    },
                    "required": ["location", "time_context"]
                }
            },
            {
                "name": "schedule_wakeup_call",
                "description": "Schedule a morning wake-up call for a user. You must include a daily briefing or itinerary in the context.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "target_extension": {"type": "STRING", "description": "The exact SIP extension to ring."},
                        "scheduled_time": {"type": "STRING", "description": "The exact date and time to initiate the call in 'YYYY-MM-DD HH:MM:SS' format."},
                        "briefing_notes": {"type": "STRING", "description": "The itinerary or notes to read to the user when they wake up."}
                    },
                    "required": ["target_extension", "scheduled_time", "briefing_notes"]
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
        
        elif name == "route_to_room":
            target = args.get("room_name")
            ext = self.session.db_manager.get_extension_for_room(target)
            if ext:
                return {"status": "success", "extension": ext}
            return {"status": "failed", "message": f"No physical extension found for room: {target}."}

        elif name == "route_to_person":
            target = args.get("person_name")
            ext = self.session.db_manager.get_extension_for_person(target)
            if ext:
                return {"status": "success", "extension": ext}
            return {"status": "failed", "message": f"No primary location found for person: {target}."}
        
        elif name == "schedule_outbound_call":
            target = args.get("target_extension")
            scheduled_time = args.get("scheduled_time")
            context = args.get("context")
            
            try:
                self.session.db_manager.schedule_callback(target, scheduled_time, context)
                return {
                    "status": "success", 
                    "message": f"Outbound call successfully scheduled to {target} at {scheduled_time}."
                }
            except Exception as e:
                return {"status": "failed", "message": f"Database error: {str(e)}"}
            
        elif name == "check_weather":
            location = args.get("location")
            # Default to Cardiff if location parsing is vague, or rely on the prompt.
            if not location or location.lower() in ["here", "home"]:
                location = "Cardiff, UK" 
                
            api_key = self.session.config.get("openweathermap_api_key")
            if not api_key:
                return {"status": "failed", "message": "Weather API key missing."}

            url = f"http://api.openweathermap.org/data/2.5/weather?q={location}&appid={api_key}&units=metric"
            
            try:
                async with aiohttp.ClientSession() as http_session:
                    async with http_session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            weather_desc = data['weather'][0]['description']
                            temp = data['main']['temp']
                            feels_like = data['main']['feels_like']
                            
                            forecast = f"{temp}°C, feels like {feels_like}°C. Conditions: {weather_desc}."
                            return {
                                "status": "success",
                                "location": location,
                                "forecast": forecast
                            }
                        else:
                            return {"status": "failed", "message": f"API returned status {response.status}"}
            except Exception as e:
                return {"status": "failed", "message": str(e)}

        elif name == "schedule_wakeup_call":
            target = args.get("target_extension")
            scheduled_time = args.get("scheduled_time")
            briefing = args.get("briefing_notes")
            
            # We reuse the robust unified Tasks queue we built earlier!
            context = f"WAKE UP CALL. Itinerary/Briefing: {briefing}"
            
            try:
                self.session.db_manager.schedule_callback(target, scheduled_time, context)
                return {
                    "status": "success", 
                    "message": f"Wake up call successfully scheduled for {scheduled_time}."
                }
            except Exception as e:
                return {"status": "failed", "message": f"Database error: {str(e)}"}
            
        return {"error": "Function not implemented."}