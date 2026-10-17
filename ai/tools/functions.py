import os
import json
import aiohttp
from typing import List
from datetime import datetime
from pydantic import BaseModel, Field
from ai.tools.registry import registry
import asyncio

# ---------------------------------------------------------
# Tool 1: Current Time
# ---------------------------------------------------------
@registry.register(
    name="get_current_time",
    description="Get the exact current server time and date.",
    log_execution=True
)
def get_current_time():
    now = datetime.now().strftime("%I:%M %p on %A, %B %d, %Y")
    return {"time": now}

# ---------------------------------------------------------
# Tool 2: Weather (Open-Meteo JSON API)
# ---------------------------------------------------------
class WeatherArgs(BaseModel):
    location: str = Field(description="The city and state/country, e.g., 'San Francisco, CA' or 'London, UK'")

@registry.register(
    name="get_weather",
    description="Get the current real-time weather forecast for a specific location.",
    args_schema=WeatherArgs,
    log_execution=True
)
async def get_weather(location: str):
    print(f"[Tools] 🌤️  Fetching weather for: {location}")
    try:
        clean_loc = location.split(',')[0].strip()
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={clean_loc}&count=1&language=en&format=json"
        
        async with aiohttp.ClientSession() as session:
            # 1. Geocode city name to lat/lon
            async with session.get(geo_url, timeout=3.0) as geo_resp:
                if geo_resp.status != 200:
                    return {"error": "Geocoding service unavailable."}
                geo_data = await geo_resp.json()
                
                results = geo_data.get("results")
                if not results:
                    return {"error": f"Location '{location}' not found."}
                
                lat = results[0]["latitude"]
                lon = results[0]["longitude"]
                resolved_name = f"{results[0].get('name')}, {results[0].get('country', '')}"

            # 2. Get current weather using coordinates
            weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
            async with session.get(weather_url, timeout=3.0) as weather_resp:
                if weather_resp.status != 200:
                    return {"error": "Weather forecast service unavailable."}
                w_data = await weather_resp.json()
                current = w_data.get("current_weather", {})
                
                temp_c = current.get("temperature")
                temp_f = round((temp_c * 9/5) + 32, 1) if temp_c is not None else "N/A"
                
                return {
                    "location": resolved_name,
                    "temperature_f": f"{temp_f}°F",
                    "temperature_c": f"{temp_c}°C",
                    "windspeed": f"{current.get('windspeed')} km/h"
                }
    except Exception as e:
        print(f"[Tools] Weather API Error: {e}")
        return {"error": "Failed to retrieve weather data."}

# ---------------------------------------------------------
# Tool: End Call
# ---------------------------------------------------------
@registry.register(
    name="end_call",
    description="Hangs up the current phone call.",
    log_execution=True
)
def end_call(context: dict):
    print("[Tools] 📞 AI requested call hangup (Queued for turn completion)")
    
    hangup_cb = context.get("hangup_callback")
    if hangup_cb:
        async def action(): await hangup_cb()
        context.setdefault("post_turn_actions", []).append(action)
        return {"status": "success", "message": "Call termination queued."}
        
    return {"error": "Hangup callback unavailable."}

# ---------------------------------------------------------
# Tool 4: Call Summary (Execution Logging Disabled)
# ---------------------------------------------------------
class TranscriptLine(BaseModel):
    speaker: str = Field(description="Who is speaking, strictly 'Caller' or 'AI'")
    text: str = Field(description="The spoken text")

class CallSummaryArgs(BaseModel):
    transcript: List[TranscriptLine] = Field(description="A sequential list of all dialogue in the call.")
    summary: str = Field(description="A 2-3 sentence executive summary of the main topics discussed and actions taken.")

@registry.register(
    name="call_summary",
    description="Generates and saves the call transcript and summary. SYSTEM ONLY. Do not attempt to use this tool until explicitly commanded by a system event.",
    args_schema=CallSummaryArgs,
    log_execution=False,
    restricted=True
)
async def call_summary(transcript: list, summary: str, context: dict):
    print("[Tools] 📝 Saving Call Summary & Structured Transcript...")
    
    end_time = datetime.now().isoformat()
    caller_name = context.get("caller_name", "Unknown")
    caller_number = context.get("caller_number", "Unknown")
    call_id = context.get("call_id", f"call_{int(datetime.now().timestamp())}")
    
    formatted_transcript = []
    for line in transcript:
        if hasattr(line, 'model_dump'):
            formatted_transcript.append(line.model_dump())
        elif isinstance(line, dict):
            formatted_transcript.append(line)
    
    summary_data = {
        "call_id": call_id,
        "direction": context.get("direction", "inbound"),
        "caller_name": caller_name,
        "caller_number": caller_number,
        "start_time": context.get("start_time"),
        "end_time": end_time,
        "tool_logs": context.get("tool_logs", []),
        "summary": summary,
        "transcript": formatted_transcript
    }
    
    os.makedirs("data/summaries", exist_ok=True)
    file_path = f"data/summaries/{caller_number}_{call_id}.json"
    
    with open(file_path, "w") as f:
        json.dump(summary_data, f, indent=4)
        
    print(f"[Tools] ✅ Summary saved to {file_path}")
    return {"status": "Summary and transcript saved successfully."}

# ---------------------------------------------------------
# Tool: Transfer Call
# ---------------------------------------------------------
class TransferArgs(BaseModel):
    target: str = Field(description="The extension number or name to transfer the call to. e.g., '6', '1001', or 'Alex'")

@registry.register(
    name="transfer_call",
    description="Transfers the current phone call to another extension or user.",
    args_schema=TransferArgs,
    log_execution=True
)
def transfer_call(target: str, context: dict):
    print(f"[Tools] 🔀 AI requested call transfer to: {target} (Queued for turn completion)")
    
    clean_target = str(target).strip().replace(" ", "-")
    transfer_cb = context.get("transfer_callback")
    
    if transfer_cb:
        async def action(): await transfer_cb(clean_target)
        context.setdefault("post_turn_actions", []).append(action)
        return {"status": "success", "message": f"Transfer to {clean_target} queued."}
    
    return {"error": "Transfer callback unavailable."}

# ---------------------------------------------------------
# Tool: Hold / Resume Call
# ---------------------------------------------------------
class HoldArgs(BaseModel):
    action: str = Field(description="Action to perform. Must be strictly 'hold' or 'resume'.")

@registry.register(
    name="toggle_hold",
    description="Puts the caller on hold, or resumes the call if they are currently on hold.",
    args_schema=HoldArgs,
    log_execution=True
)
def toggle_hold(action: str, context: dict):
    print(f"[Tools] ⏸️ AI requested hold action: {action}")
    
    hold_cb = context.get("hold_callback")
    if hold_cb:
        is_hold = action.lower() == "hold"
        hold_cb(is_hold)
        return {"status": "success", "message": f"Call is now {'on hold' if is_hold else 'resumed'}."}
    
    return {"error": "Hold callback unavailable."}