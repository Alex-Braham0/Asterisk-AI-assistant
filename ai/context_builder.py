import datetime
import zoneinfo

class ContextBuilder:
    @staticmethod
    def build_initial_prompt(base_system_prompt, direction="inbound", caller_info=None, endpoint_data=None, memory_content=""):
        user_timezone = endpoint_data.get('default_timezone', 'Europe/London') if endpoint_data else 'Europe/London'
        tz = zoneinfo.ZoneInfo(user_timezone)
        now = datetime.datetime.now(tz).strftime('%A, %B %d, %Y %I:%M %p %Z')
        
        conditional_directives = ""
        if endpoint_data and endpoint_data.get('default_user_name'):
            owner = endpoint_data.get('default_user_name')
            conditional_directives += f"- PREDICTIVE IDENTITY: This device belongs to {owner}. Proceed under the absolute assumption you are speaking to {owner}. DO NOT ask for their name or verify their identity. Just greet them naturally.\n"
            conditional_directives += f"- OVERRIDE: If the caller introduces themselves as someone else, silently use the 'set_active_user' tool to swap context.\n"
        elif not endpoint_data or not endpoint_data.get('physical_location'):
            conditional_directives += "- UNKNOWN DEVICE: Ask who is calling and where this phone is physically located.\n"
        else:
             conditional_directives += "- SHARED STATION: Ask who is calling before answering complex requests.\n"

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
3. SILENT EXECUTION: You are equipped with backend database tools. You MUST use them completely silently. Never say 'Let me update that', 'I have saved your preference', 'I am looking that up', or 'I have updated the hardware context'. Simply respond to the user naturally as if the action was an internal thought.
4. ERROR HANDLING: If a backend tool returns a 'failed' status, a 'collision', or an error, you must gracefully apologize to the caller, state that the specific system or action is unavailable right now, and seamlessly pivot the conversation. Do not read the technical error payload out loud.
{conditional_directives.strip()}
</strict_directives>"""

        return prompt.strip()