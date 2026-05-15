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
        return [
            {
                "name": "end_call",
                "description": "Hangs up the phone. Use ONLY when the conversation is completely finished.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "reason": {"type": "STRING", "description": "Brief reason for ending the call (e.g., 'User said goodbye')."}
                    },
                    "required": ["reason"]
                }
            },
            {
                "name": "submit_call_summary",
                "description": "DO NOT USE MANUALLY. Only use when explicitly commanded by the system.",
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
                "name": "check_weather",
                "description": "Fetches the weather forecast. You MUST sanitize the location name.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "location": {
                            "type": "STRING", 
                            "description": "CRITICAL: The CITY NAME ONLY. Strip out conversational words like 'town centre', 'downtown', or 'area'. (e.g., Good: 'Cardiff'. Bad: 'Cardiff town centre')."
                        },
                        "time_context": {
                            "type": "STRING", 
                            "description": "The target time (e.g., 'tonight', 'tomorrow morning', 'now')."
                        }
                    },
                    "required": ["location", "time_context"]
                }
            },
            {
                "name": "lookup_directory",
                "description": "Look up the exact numeric SIP extension for a specific person or room.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "search_name": {
                            "type": "STRING", 
                            "description": "The core name to search for. Strip away possessives and filler words. (e.g., Good: 'Ash'. Bad: 'Ash\\'s room')."
                        }
                    },
                    "required": ["search_name"]
                }
            },
            {
                "name": "get_full_directory",
                "description": "Retrieves the entire phonebook of active extensions.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "request": {"type": "STRING", "description": "Pass the exact string 'all'."}
                    },
                    "required": ["request"]
                }
            },
            {
                "name": "check_scheduled_calls",
                "description": "Look up all scheduled outbound calls for a specific numeric extension.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "target_extension": {
                            "type": "STRING", 
                            "description": "The exact NUMERIC SIP extension. NEVER use a name."
                        }
                    },
                    "required": ["target_extension"]
                }
            },
            {
                "name": "cancel_scheduled_call",
                "description": "Cancel a specific scheduled call. You MUST use check_scheduled_calls first to get the task_id.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "task_id": {
                            "type": "INTEGER", 
                            "description": "The exact numerical task_id returned from check_scheduled_calls."
                        }
                    },
                    "required": ["task_id"]
                }
            },
            {
                "name": "schedule_outbound_call",
                "description": "Schedule a future outbound call. You MUST use a numeric SIP extension.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "target_extension": {
                            "type": "STRING", 
                            "description": "The EXACT NUMERIC SIP extension (e.g., '1001'). NEVER use names."
                        },
                        "scheduled_time": {
                            "type": "STRING", 
                            "description": "The precise date and time in 'YYYY-MM-DD HH:MM:SS' format."
                        },
                        "context": {
                            "type": "STRING", 
                            "description": "A clear, concise reason for the call."
                        }
                    },
                    "required": ["target_extension", "scheduled_time", "context"]
                }
            }
        ]

    async def execute_tool(self, name, args):
        """Executes the Python logic for a requested tool."""
        
        if name == "end_call":
            reason = args.get("reason", "No reason provided")
            print(f"[ToolRegistry] AI initiated call termination. Reason: {reason}")
            self.session.drop_call()
            
            asyncio.create_task(self.session.trigger_summary(
                reason="You (the AI) have ended the call via the 'end_call' tool."
            ))
            return {"status": "success", "message": "SIP line disconnected. Awaiting summary execution."}
            
        elif name == "submit_call_summary":
            print(f"\n--- Final Call Summary (JSON) ---\n{json.dumps(args, indent=2)}\n---------------------------------")
            self.session.drop_call()
            self.session.terminate_bridge()
            return None
        
        elif name == "lookup_directory":
            raw_target = args.get("search_name", "")
            
            # --- BACKEND SANITIZATION ---
            # Remove possessives like "'s" so "Ash's" becomes "Ash"
            clean_target = raw_target.lower().replace("'s", "").replace("room", "").strip()
            
            result = self.session.db_manager.lookup_extension(clean_target)
            if result:
                return {"status": "success", "data": result}
            return {"status": "failed", "message": f"No endpoint found matching '{clean_target}'."}

        elif name == "get_full_directory":
            directory = self.session.db_manager.get_full_directory()
            return {"status": "success", "directory": directory}

        elif name == "schedule_outbound_call":
            target = str(args.get("target_extension", ""))
            scheduled_time = args.get("scheduled_time")
            context = args.get("context")
            
            # The Safety Net: Auto-resolve if Winston hallucinates a name instead of a number
            if not target.replace('*', '').isdigit():
                print(f"[ToolRegistry] Warning: AI passed non-numeric target '{target}'. Attempting auto-resolve.")
                resolved = self.session.db_manager.lookup_extension(target)
                
                if resolved:
                    target = resolved['extension']
                    print(f"[ToolRegistry] Auto-resolved to extension '{target}'.")
                else:
                    return {
                        "status": "failed", 
                        "message": f"Could not schedule. '{target}' is not a numeric extension and could not be resolved."
                    }
            
            try:
                self.session.db_manager.schedule_callback(target, scheduled_time, context)
                return {"status": "success", "message": f"Outbound call scheduled to numeric extension {target}."}
            except Exception as e:
                return {"status": "failed", "message": f"Database error: {str(e)}"}
            
        elif name == "check_scheduled_calls":
            target = str(args.get("target_extension", ""))
            
            # Safety net: resolve name to number if hallucinated
            if not target.isdigit():
                resolved = self.session.db_manager.lookup_extension(target)
                if resolved:
                    target = resolved['extension']
                else:
                    return {"status": "failed", "message": f"Could not find numeric extension for {target}."}
            
            calls = self.session.db_manager.get_pending_calls(target)
            if calls:
                return {"status": "success", "pending_calls": calls}
            else:
                return {"status": "success", "message": "No pending calls found for this extension."}

        elif name == "cancel_scheduled_call":
            task_id = args.get("task_id")
            
            if not isinstance(task_id, int):
                return {"status": "failed", "message": "task_id must be a valid integer."}
                
            try:
                cancelled_count = self.session.db_manager.cancel_task_by_id(task_id)
                if cancelled_count > 0:
                    return {"status": "success", "message": f"Successfully cancelled task ID {task_id}."}
                else:
                    return {"status": "failed", "message": f"Task ID {task_id} not found or already processed/cancelled."}
            except Exception as e:
                return {"status": "failed", "message": f"Database error: {str(e)}"}

        elif name == "check_weather":
            raw_location = args.get("location", "Cardiff")
            time_context = args.get("time_context")
            
            # --- BACKEND SANITIZATION ---
            # Force lowercase and strip common conversational fillers that break the API
            clean_location = raw_location.lower()
            fillers = [" town centre", " city centre", " downtown", " area", " central"]
            for filler in fillers:
                clean_location = clean_location.replace(filler, "")
            clean_location = clean_location.strip()
            
            api_key = self.session.config.get("openweathermap_api_key")
            if not api_key:
                return {"status": "failed", "message": "Weather API key missing."}

            url = f"http://api.openweathermap.org/data/2.5/weather?q={clean_location}&appid={api_key}&units=metric"
            
            try:
                import aiohttp
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
                                "location": clean_location.title(),
                                "forecast": forecast
                            }
                        else:
                            return {"status": "failed", "message": f"API returned status {response.status} for location '{clean_location}'"}
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