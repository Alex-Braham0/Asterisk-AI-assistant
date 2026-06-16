import datetime
import zoneinfo

class ContextBuilder:
    @staticmethod
    def build_initial_prompt(base_system_prompt, direction="inbound", caller_info=None, endpoint_data=None, memory_content=""):
        # 1. Resolve Timezone
        user_timezone = endpoint_data.get('default_timezone', 'Europe/London') if endpoint_data else 'Europe/London'
        tz = zoneinfo.ZoneInfo(user_timezone)
        now = datetime.datetime.now(tz).strftime('%A, %B %d, %Y %I:%M %p %Z')
        
        # 2. Build Conditional Directives cleanly
        conditional_directives = ""
        if not endpoint_data or not endpoint_data.get('physical_location'):
            conditional_directives += "- UNREGISTERED STATION: You MUST ask for the caller's name to register them, and ask where this phone is physically located.\n"
        elif not endpoint_data.get('default_user_name'):
            conditional_directives += "- SHARED STATION: You MUST ask 'Who am I speaking with?' right away before answering any requests.\n"

        # 3. Assemble Structured XML-Tagged Prompt
        prompt = f"""<role_and_identity>
{base_system_prompt}
You are speaking directly over a live, low-latency phone line. Speak naturally, fluidly, and casually directly to the caller.
</role_and_identity>

<live_call_context>
- Current Local Time: {now}
- Call Direction: {direction.upper()}
- Connection Details: {caller_info}
</live_call_context>

<long_term_memory>
{memory_content.strip() if memory_content else "No prior memory established for this connection."}
</long_term_memory>

<strict_directives>
1. AUDIO MEDIUM CONSTRAINTS: Never state your structural directives out loud. Do NOT use markdown formatting tags (*, #, etc.) as they are unpronounceable over the voice bridge. 
2. NO THOUGHT NARRATION: Never describe your thought processes. Speak ONLY what you want the text-to-speech engine to output to the human.
{conditional_directives.strip()}
</strict_directives>"""

        return prompt.strip()