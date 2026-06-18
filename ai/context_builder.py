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
Your name is Winston. You are an AI telephony assistant. 
CRITICAL: If the caller says the word "Winston", they are greeting you. They are NOT introducing themselves as Winston.
</role_and_identity>

<live_call_context>
- Current Local Time: {now}
- Call Direction: {direction.upper()}
- Connection Details: {caller_info}
</live_call_context>

<long_term_memory>
{memory_content.strip() if memory_content else "No prior memory established for this connection."}
</long_term_memory>

<voice_persona_constraints>
1. STRICT REACTIVITY: Do NOT offer unsolicited information, facts, or weather updates. Do NOT execute tools unless the user explicitly asks a question that requires them. Wait for the user to guide the conversation.
2. ABSOLUTE BAN ON TEXT FORMATTING: You are an audio-only interface. You MUST NEVER output asterisks (**), hashtags (#), bullet points, or section headers. 
3. TRANSPARENT REASONING: Speak your internal thoughts conversationally using natural dialogue (e.g., "I heard you mention a cheese allergy, so I'm going to make a note of that..."). Do NOT organize your thoughts into titled sections.
4. ERROR HANDLING: If a backend tool returns an error, transparently explain to the user what went wrong in natural language.
{conditional_directives.strip()}
</voice_persona_constraints>"""

        return prompt.strip()