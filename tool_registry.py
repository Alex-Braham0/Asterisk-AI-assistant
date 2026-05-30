import json
import asyncio
import aiohttp
import datetime
import zoneinfo
import time

class ToolRegistry:
    def __init__(self, session):
        self.session = session
        
        # Define access level requirements for each tool (0=Guest, 10=Standard, 100=Admin)
        self.tool_auth_requirements = {
            "end_call": 0,
            "submit_call_summary": 0,
            "check_weather": 0,
            "lookup_directory": 0,
            "get_full_directory": 10,
            "check_scheduled_calls": 10,
            "cancel_scheduled_call": 10,
            "schedule_outbound_call": 10,
            "send_dtmf": 10,
            "transfer_call": 0,
            "schedule_wakeup_call": 10
        }

    def _check_auth(self, name):
        """
        Stub for evaluating access levels.
        Assumes self.session.active_user_level exists (defaults to 10 for now).
        """
        required_level = self.tool_auth_requirements.get(name, 100)
        user_level = getattr(self.session, 'active_user_level', 10)
        
        # Force allow everything for current build phase as requested
        return True
        # Future enforcement: return user_level >= required_level

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
                "description": "DO NOT USE MANUALLY. Use when commanded by the system. Provide data for the Memory Manager AI.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "call_synopsis": {
                            "type": "STRING", 
                            "description": "A very brief 1-2 sentence summary of what occurred."
                        },
                        "proposed_memory_updates": {
                            "type": "ARRAY", 
                            "items": {"type": "STRING"},
                            "description": "Specific new facts to add to permanent memory. If nothing important was learned, output ['None']."
                        }
                    },
                    "required": ["call_synopsis", "proposed_memory_updates"]
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
            },
            {
                "name": "send_dtmf",
                "description": "Presses a key on the phone's dialpad. Useful for navigating automated menus or triggering PBX extension codes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                    "digit": {
                        "type": "string",
                        "description": "A single character to press: 0-9, *, or #"
                    }
                    },
                    "required": ["digit"]
                }
            },
            {
                "name": "transfer_call",
                "description": "Transfers the current call to another extension. Use this when the user asks to speak to a human or a specific person.",
                "parameters": {
                    "type": "object",
                    "properties": {
                    "target_extension": {
                        "type": "string",
                        "description": "The numeric extension to transfer to (e.g., '6' or '16')."
                    },
                    "reason": {
                        "type": "string",
                        "description": "The reason for the transfer."
                    }
                    },
                    "required": ["target_extension"]
                }
            },
            {
                "name": "set_active_user",
                "description": "Call this immediately when a user states their name on a shared phone. It loads their personal memory profile and timezone.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "user_name": {"type": "STRING", "description": "The name of the user speaking."}
                    },
                    "required": ["user_name"]
                }
            }
        ]

    async def execute_tool(self, name, args):
        """Executes the Python logic for a requested tool."""
        
        if not self._check_auth(name):
            return {"status": "failed", "message": f"Authorization denied for tool {name}."}

        if name == "end_call":
            reason = args.get("reason", "No reason provided")
            print(f"[ToolRegistry] AI initiated call termination. Reason: {reason}")
            asyncio.create_task(self.session.drain_audio_and_drop_call())
            
            asyncio.create_task(self.session.trigger_summary(
                reason="You (the AI) have ended the call via the 'end_call' tool."
            ))
            return {"status": "success", "message": "SIP line disconnected. Awaiting summary execution."}
            
        elif name == "submit_call_summary":
            extension = getattr(self.session, 'target_extension', 'Unknown')
            
            if self.session.bridge_start_time:
                duration_seconds = int(time.time() - self.session.bridge_start_time)
                args["start_time"] = self.session.bridge_start_datetime
                args["duration_seconds"] = duration_seconds
            else:
                args["start_time"] = "Unknown"
                args["duration_seconds"] = 0
                
            args["extension"] = extension

            user_tz = await self.session.db_manager.get_user_timezone(extension)
            args["timezone"] = user_tz

            print(f"[ToolRegistry] Summary received. Queuing update for extension {extension} (Duration: {args.get('duration_seconds')}s)...")
            
            asyncio.create_task(self.session.db_manager.spool_call_summary(extension, args))
            
            self.session.drop_call()
            self.session.terminate_bridge()
            return None
        
        elif name == "lookup_directory":
            raw_target = args.get("search_name", "")
            clean_target = raw_target.lower().replace("'s", "").replace("room", "").strip()
            
            result = await self.session.db_manager.lookup_extension(clean_target)
            if result:
                return {"status": "success", "data": result}
            return {"status": "failed", "message": f"No endpoint found matching '{clean_target}'."}

        elif name == "get_full_directory":
            directory = await self.session.db_manager.get_full_directory()
            return {"status": "success", "directory": directory}

        elif name == "schedule_outbound_call":
            target = args.get("target_extension")
            scheduled_local_str = args.get("scheduled_time")
            context = args.get("context")
            
            try:
                user_tz_str = await self.session.db_manager.get_user_timezone(target)
                local_tz = zoneinfo.ZoneInfo(user_tz_str)
                
                naive_dt = datetime.datetime.strptime(scheduled_local_str, "%Y-%m-%d %H:%M:%S")
                local_dt = naive_dt.replace(tzinfo=local_tz)
                
                # Convert to UTC and strip the tzinfo to create a naive UTC datetime
                utc_dt = local_dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
                
                print(f"[ToolRegistry] Translating Time: {scheduled_local_str} ({user_tz_str}) -> {utc_dt} (UTC)")
                
                # Pass the native object directly
                await self.session.db_manager.schedule_callback(target, utc_dt, context) 
                
                return {
                    "status": "success", 
                    "message": f"Call successfully scheduled for {scheduled_local_str} local time."
                }
            except Exception as e:
                return {"status": "failed", "message": f"Scheduling error: {str(e)}"}
            
        elif name == "check_scheduled_calls":
            target = str(args.get("target_extension", ""))
            
            if not target.isdigit():
                resolved = await self.session.db_manager.lookup_extension(target)
                if resolved:
                    target = resolved['extension']
                else:
                    return {"status": "failed", "message": f"Could not find numeric extension for {target}."}
            
            calls = await self.session.db_manager.get_pending_calls(target)
            if calls:
                return {"status": "success", "pending_calls": calls}
            else:
                return {"status": "success", "message": "No pending calls found for this extension."}

        elif name == "cancel_scheduled_call":
            task_id = args.get("task_id")
            
            if not isinstance(task_id, int):
                return {"status": "failed", "message": "task_id must be a valid integer."}
                
            try:
                cancelled_count = await self.session.db_manager.cancel_task_by_id(task_id)
                if cancelled_count > 0:
                    return {"status": "success", "message": f"Successfully cancelled task ID {task_id}."}
                else:
                    return {"status": "failed", "message": f"Task ID {task_id} not found or already processed/cancelled."}
            except Exception as e:
                return {"status": "failed", "message": f"Database error: {str(e)}"}

        elif name == "check_weather":
            raw_location = args.get("location", "Cardiff")
            time_context = args.get("time_context")
            
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
            
            context = f"WAKE UP CALL. Itinerary/Briefing: {briefing}"
            
            try:
                await self.session.db_manager.schedule_callback(target, scheduled_time, context)
                return {
                    "status": "success", 
                    "message": f"Wake up call successfully scheduled for {scheduled_time}."
                }
            except Exception as e:
                return {"status": "failed", "message": f"Database error: {str(e)}"}
            
        elif name == "send_dtmf":
            digit = args.get("digit")
            try:
                self.session.engine.send_dtmf(digit)
                return {"status": "success", "message": f"Successfully pressed {digit}."}
            except Exception as e:
                return {"status": "failed", "message": f"Failed to send DTMF: {e}"}
        
        elif name == "transfer_call":
            target = args.get("target_extension")
            reason = args.get("reason", "No reason provided")
            print(f"[ToolRegistry] AI initiated transfer to {target}. Reason: {reason}")
            
            try:
                self.session.engine.transfer_call(target)
                
                asyncio.create_task(self.session.trigger_summary(
                    reason=f"Call transferred to {target}. Reason: {reason}"
                ))
                return {"status": "success", "message": f"Transferring to {target} initiated."}
            except Exception as e:
                return {"status": "failed", "message": f"Transfer failed: {e}"}
            
        elif name == "set_active_user":
            user_name = args.get("user_name")
            
            # Fetch user from PostgreSQL
            query = "SELECT id, access_level, current_timezone FROM Users WHERE name ILIKE $1 LIMIT 1"
            async with self.session.db_manager.pool.acquire() as conn:
                user_data = await conn.fetchrow(query, user_name)

            if not user_data:
                return {"status": "failed", "message": f"User '{user_name}' not found in database."}

            # Elevate session access level
            self.session.active_user_level = user_data['access_level']
            
            # Read user's markdown file
            user_memory = await self.session.db_manager.get_user_memory(user_name)
            
            # Inject new context into the live stream
            injection_text = (
                f"IDENTITY CONFIRMED. Active User is now {user_name}. "
                f"New Timezone is {user_data['current_timezone']}. "
                f"USER MEMORY PROFILE: {user_memory}"
            )
            asyncio.create_task(self.session.gemini_client.send_system_event(injection_text))
            
            return {"status": "success", "message": f"Context switched to {user_name}."}
            
        return {"error": "Function not implemented."}