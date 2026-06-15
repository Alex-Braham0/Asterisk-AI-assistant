import datetime
import zoneinfo

class ContextBuilder:
    @staticmethod
    def build_initial_prompt(base_system_prompt, direction="inbound", caller_info=None, endpoint_data=None, memory_content=""):
        user_timezone = endpoint_data.get('default_timezone', 'Europe/London') if endpoint_data else 'Europe/London'
        
        tz = zoneinfo.ZoneInfo(user_timezone)
        now = datetime.datetime.now(tz).strftime('%A, %B %d, %Y %I:%M %p %Z')
        
        context_prompt = (
            f"You are Winston, the home's dedicated concierge. Speak naturally and directly to the caller. "
            f"Current Local Time: {now}. Call Direction: {direction.upper()}. "
            f"Call Details: {caller_info}.\n\n"
            f"Profile Memory:\n{memory_content}\n\n"
            f"{base_system_prompt}\n\n"
            f"CRITICAL: Do not output markdown headers or speak your internal thoughts out loud. Speak only what Winston says to the caller.\n\n"
        )
        
        if not endpoint_data or not endpoint_data.get('physical_location'):
            context_prompt += (
                "This caller and device are unregistered. Politely ask for their name "
                "during the conversation so you can use the 'register_new_user' tool, and ask where this "
                "phone is located so you can use the 'update_endpoint_context' tool.\n"
            )
            
        if endpoint_data and not endpoint_data.get('default_user_name'):
            context_prompt += (
                "This is a shared phone. Politely ask 'Who am I speaking with?' at the start of the call "
                "before fulfilling requests, then use 'search_users' and 'set_active_user'.\n"
            )
            
        if direction == "outbound":
            context_prompt += (
                "You are initiating this call. Greet the person immediately when they answer. If you hit a voicemail beep, "
                "leave a short message and hang up using 'end_call'.\n"
            )
            
        return context_prompt