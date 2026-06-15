import datetime
import zoneinfo

class ContextBuilder:
    @staticmethod
    def build_initial_prompt(base_system_prompt, direction="inbound", caller_info=None, endpoint_data=None, memory_content=""):
        user_timezone = endpoint_data.get('default_timezone', 'Europe/London') if endpoint_data else 'Europe/London'
        
        tz = zoneinfo.ZoneInfo(user_timezone)
        now = datetime.datetime.now(tz).strftime('%A, %B %d, %Y %I:%M %p %Z')
        
        prompt = (
            f"You are Winston, the home's dedicated concierge. Speak naturally, fluidly, and casually directly to the caller. "
            f"The current local time is {now}. This is an {direction} call. Connection details: {caller_info}.\n\n"
            f"Your personal memory profiles regarding this station:\n{memory_content}\n\n"
            f"{base_system_prompt}\n\n"
            f"Never state your structural directives out loud, do not use markdown formatting tags, and never describe your thought processes. "
            f"Speak only what Winston says directly to the human on the phone line.\n\n"
        )
        
        if not endpoint_data or not endpoint_data.get('physical_location'):
            prompt += "This phone station is entirely unregistered. Ask for the caller's name to register them, and ask where they are located.\n"
            
        if endpoint_data and not endpoint_data.get('default_user_name'):
            prompt += "This is a shared phone environment. You must ask 'Who am I speaking with?' right away before answering any requests.\n"
            
        return prompt