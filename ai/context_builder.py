import datetime
import zoneinfo
from typing import Optional, Dict, Any

class ContextBuilder:
    def __init__(self, config: Any):
        self.config = config
        self.base_prompt = getattr(config, "system_prompt", "You are a helpful phone assistant.")

    def build_system_instruction(
        self, 
        direction: str,
        caller_info: str,
        user_data: Optional[Dict[str, Any]] = None, 
        endpoint_data: Optional[Dict[str, Any]] = None
    ) -> str:
        
        user_timezone = endpoint_data.get('default_timezone', 'Europe/London') if endpoint_data else 'Europe/London'
        tz = zoneinfo.ZoneInfo(user_timezone)
        now = datetime.datetime.now(tz).strftime('%A, %B %d, %Y %I:%M %p %Z')

        greeting_protocol = "<greeting_and_caller_id_protocol>\n"
        if endpoint_data:
            device_type = endpoint_data.get('device_type', 'UNKNOWN')
            default_user = endpoint_data.get('default_user_name')
            associated_users = endpoint_data.get('associated_users', [])
            display_name = endpoint_data.get('display_name', 'Unknown Location')
            
            if device_type in ['MOBILE', 'STATIC_PRIVATE'] and default_user and len(associated_users) <= 1:
                greeting_protocol += f"1. CALLER ID: This is {default_user}'s personal device. Proceed under the absolute assumption you are speaking to {default_user}.\n"
                greeting_protocol += f"2. GREETING: Answer the phone warmly and use their name immediately. Do NOT ask who is calling or verify their identity.\n"
            elif device_type == 'STATIC_PRIVATE' and len(associated_users) > 1:
                names = " or ".join(associated_users)
                greeting_protocol += f"1. MULTI-USER CALLER ID: This phone belongs to a specific group of people: {names}.\n"
                greeting_protocol += f"2. GREETING: Assume it is one of them. Greet them casually and gently clarify who it is.\n"
            elif device_type == 'STATIC_SHARED':
                if associated_users:
                    names = ", ".join(associated_users)
                    greeting_protocol += f"1. SHARED LINE PREDICTION: This is a shared phone located at '{display_name}'. The known users are: {names}.\n"
                    greeting_protocol += f"2. GREETING: The primary user is {default_user}. Greet them friendly, assuming it's one of them, and gently ask who you're speaking with.\n"
                else:
                    greeting_protocol += f"1. SHARED LOCATION: This is a generic shared phone at '{display_name}'.\n"
                    greeting_protocol += f"2. GREETING: Answer politely, mention the location, and ask who is calling.\n"
        else:
            greeting_protocol += "1. UNKNOWN DEVICE: This call is coming from an unmapped or generic extension.\n"
            greeting_protocol += "2. GREETING: Use a standard, professional greeting and politely ask for their name.\n"

        greeting_protocol += "3. IDENTITY OVERRIDE: If the caller explicitly states they are someone else, silently use the 'resolve_and_switch_user' tool to correct your context.\n"
        greeting_protocol += "</greeting_and_caller_id_protocol>"

        sections = [
            f"<role_and_identity>\n{self.base_prompt}\nYour name is Winston. You are an AI telephony assistant.\nCRITICAL: If the caller says the word 'Winston', they are greeting you. They are NOT introducing themselves as Winston.\nDo not hang up just because a tool has completed.\n</role_and_identity>",
            (
                f"<live_call_context>\n"
                f"- Current Local Time: {now}\n"
                f"- Call Direction: {direction.upper()}\n"
                f"- Connection Details: {caller_info}\n"
                f"</live_call_context>"
            ),
            self._build_telephony_context(endpoint_data),
            self._build_user_memory_context(user_data),
            greeting_protocol,
            self._build_voice_persona_constraints(),
            self._build_identity_switching_instructions()
        ]
        
        return "\n\n".join([s for s in sections if s])

    def build_headless_instruction(
        self, 
        mission_directive: str,
        user_data: Optional[Dict[str, Any]] = None
    ) -> str:
        sections = [
            f"<role_and_identity>\n{self.base_prompt}\nYou are a headless, autonomous AI agent executing a background mission. You are NOT currently connected to an audio phone line. You must achieve your objective using your available tools.\n</role_and_identity>",
            f"<mission_directive>\n{mission_directive}\n</mission_directive>",
            self._build_user_memory_context(user_data),
            self._build_headless_directives()
        ]
        return "\n\n".join([s for s in sections if s])

    def _build_telephony_context(self, endpoint_data: Optional[Dict[str, Any]]) -> str:
        if not endpoint_data:
            return "<telephony_routing_context>\nUnknown hardware endpoint.\n</telephony_routing_context>"
        return (
            f"<telephony_routing_context>\n"
            f"- Connected Extension: {endpoint_data.get('extension', 'Unknown')}\n"
            f"- Device Hardware Profile Name: {endpoint_data.get('display_name', 'Unknown')}\n"
            f"- Device Type: {endpoint_data.get('device_type', 'UNKNOWN')}\n"
            f"</telephony_routing_context>"
        )

    def _build_user_memory_context(self, user_data: Optional[Dict[str, Any]]) -> str:
        if not user_data:
            return (
                "<long_term_memory>\n"
                "No active human identity has been mapped to this call session yet.\n"
                "Treat this caller as an unverified guest until they provide a name.\n"
                "</long_term_memory>"
            )
        return (
            f"<long_term_memory>\n"
            f"- Database User ID: {user_data.get('id')}\n"
            f"- Resolved Name: {user_data.get('name')}\n"
            f"--- PUBLIC MEMORY ---\n"
            f"{user_data.get('public_memory', 'No public memory profile created yet.')}\n\n"
            f"--- PRIVATE MEMORY ---\n"
            f"{user_data.get('private_memory', 'No private memory profile created yet.')}\n"
            f"</long_term_memory>"
        )

    def _build_voice_persona_constraints(self) -> str:
        return (
            "<voice_persona_constraints>\n"
            "1. STRICT REACTIVITY: Do NOT offer unsolicited information or facts. Wait for the user to guide the conversation.\n"
            "2. NO FOURTH WALL BREAKS: Never mention your 'memory', 'database', 'system', or 'profiles' to the user. Act like a human.\n"
            "3. NO AI DISCLAIMERS: You are fully authorized to discuss the user's private notes with them. Never say 'my programming prevents me' or 'as an AI'. If you don't know something, just say 'I don't have that in my notes.'\n"
            "4. EFFICIENT MEMORY PROPOSALS: When summarizing the call, ONLY propose memory updates for confirmed, newly established facts. NEVER propose updates to state that something is 'unknown,' 'missing,' or 'not noted.'\n"
            "5. NATURAL CALL TERMINATION: You have full authority to end the call. If the user indicates they are finished, politely say goodbye, pause for 1 second, then invoke the `end_call` tool immediately.\n"
            "6. ALWAYS SPEAK FIRST: The moment a call connects, you MUST immediately speak and greet the user. Do NOT wait in silence.\n"
            "7. STRICT MODALITY ISOLATION: You are physically incapable of speaking and executing a tool call in the same response turn. If you need to ask the user a question to clarify a tool parameter, you MUST speak the question and yield the turn.\n"
            "8. NO PARALLEL TOOL CALLING: You must never invoke multiple tools simultaneously. Issue one tool, wait for the system to return the JSON response, and only then issue the next.\n"
            "</voice_persona_constraints>"
        )

    def _build_identity_switching_instructions(self) -> str:
        return (
            "<identity_resolution_protocol>\n"
            "1. If a caller introduces themselves mid-call, you MUST immediately verify or swap profiles.\n"
            "2. NEVER execute the 'set_active_user' tool directly when a name is given. You do not possess the integer Database User ID required by its schema.\n"
            "3. You MUST call the 'resolve_and_switch_user' tool. Pass the string name or extension provided by the caller into the tool's parameters.\n"
            "</identity_resolution_protocol>"
        )

    def _build_headless_directives(self) -> str:
        return (
            "<strict_directives>\n"
            "1. TRUST PROVIDED NUMBERS: If your mission explicitly includes a phone number (e.g., 'extension 6'), use `execute_outbound_dial` immediately.\n"
            "2. ASYNCHRONOUS DIALING: The `execute_outbound_dial` tool will return immediately while the phone is ringing. You MUST wait silently. Do not generate any spoken text until you receive the 'CALL CONNECTED' system event.\n"
            "3. THE CONVERSATION: When the call connects, you must converse naturally. Do not end the call immediately.\n"
            "4. ENDING THE CALL: Once the conversation reaches a natural conclusion, YOU must invoke the `end_call` tool to hang up the line.\n"
            "5. COMPLETING THE MISSION: Only AFTER `end_call` has successfully executed, or if the human hangs up on you (notified via system event), you must invoke the `mark_mission_complete` tool to terminate your background session.\n"
            "6. PARALLEL TOOL CALLING PROHIBITED: Never invoke multiple tools simultaneously. Issue one tool, wait for the JSON response, then issue the next.\n"
            "</strict_directives>"
        )