import json
from typing import Optional, Dict, Any

class ContextBuilder:
    def __init__(self, config: Any):
        self.config = config
        self.base_prompt = getattr(config, "system_prompt", "You are a helpful phone assistant.")

    def build_system_instruction(
        self, 
        user_data: Optional[Dict[str, Any]] = None, 
        endpoint_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Dynamically constructs the system prompt containing baseline behavior,
        current endpoint/extension routing context, and active user memory profile.
        """
        sections = [
            f"# BASE SYSTEM PROMPT\n{self.base_prompt}",
            self._build_telephony_context(endpoint_data),
            self._build_user_memory_context(user_data),
            self._build_identity_switching_instructions()
        ]
        
        return "\n\n---\n\n".join([s for s in sections if s])

    def _build_telephony_context(self, endpoint_data: Optional[Dict[str, Any]]) -> Optional[str]:
        if not endpoint_data:
            return None
            
        return (
            "# CURRENT TELEPHONY ROUTING CONTEXT\n"
            f"- Connected Extension: {endpoint_data.get('extension', 'Unknown')}\n"
            f"- Device Hardware Profile Name: {endpoint_data.get('display_name', 'Unknown')}\n"
            f"- Device Type: {endpoint_data.get('device_type', 'UNKNOWN')}"
        )

    def _build_user_memory_context(self, user_data: Optional[Dict[str, Any]]) -> str:
        if not user_data:
            return (
                "# ACTIVE USER MEMORY PROFILE\n"
                "No active human identity has been mapped to this call session yet.\n"
                "Treat this caller as an unverified guest until they provide a name."
            )
            
        return (
            "# ACTIVE USER MEMORY PROFILE\n"
            f"- Database User ID: {user_data.get('id')}\n"
            f"- Resolved Name: {user_data.get('name')}\n"
            f"- Core Contact Details: {user_data.get('contact_info', 'None listed')}\n\n"
            f"## Public Memory (Shared Professional Facts):\n"
            f"{user_data.get('public_memory', 'No public memory profile created yet.')}\n\n"
            f"## Private Memory (Personal Context & Interaction Notes):\n"
            f"{user_data.get('private_memory', 'No private memory profile created yet.')}"
        )

    def _build_identity_switching_instructions(self) -> str:
        return (
            "# CRITICAL INSTRUCTIONS FOR IDENTITY RESOLUTION AND SWITCHING\n"
            "1. If a caller introduces themselves mid-call (e.g., 'Hey, it's Alex', 'This is Sarah from accounting', "
            "or 'I am actually calling from extension 105'), you MUST immediately verify or swap profiles.\n"
            "2. NEVER attempt to execute the 'set_active_user' tool directly when a name is given. You do not possess "
            "the integer Database User ID required by its schema, and guessing will cause a fatal execution fault.\n"
            "3. Instead, you MUST immediately call the 'resolve_and_switch_user' tool. Pass the string name or extension "
            "provided by the caller into the tool's parameters.\n"
            "4. The system will handle searching the records, auto-creating a new profile if they do not exist, and "
            "seamlessly hot-swapping your memory layers in the background via a WebSocket state update event.\n"
            "5. After running the tool, proceed natively with the conversation utilizing their updated identity facts."
        )