import aiohttp
from .base import BaseTool

class CheckWeather(BaseTool):
    name = "check_weather"
    description = "Fetches the weather forecast. You MUST format the location correctly."
    auth_level = 0

    parameters = {
        "type": "OBJECT",
        "properties": {
            "location": {
                "type": "STRING", 
                "description": "The exact official city name ONLY. Do not include suffixes, neighborhoods, or conversational filler."
            },
            "time_context": {
                "type": "STRING", 
                "description": "The target time (e.g., 'tonight', 'tomorrow morning', 'now')."
            }
        },
        "required": ["location", "time_context"]
    }

    async def execute(self, session, args):
        clean_location = args.get("location", "Cardiff").strip().lower()
        
        api_key = session.config.get("openweathermap_api_key")
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