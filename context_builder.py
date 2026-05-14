import datetime

class ContextBuilder:
    @staticmethod
    def build_initial_prompt(base_system_prompt, direction="inbound", caller_info=None):
        now = datetime.datetime.now().strftime('%A, %B %d, %Y %I:%M %p')
        
        header = (
            "========================================\n"
            "REAL-TIME CALL CONTEXT\n"
            f"Current Date/Time: {now}\n"
            f"Call Direction: {direction.upper()}\n"
            f"Target/Context: {caller_info}\n"
            "========================================\n\n"
        )
        
        behavioral_constraints = (
            "PERSONA & VOICE: You are Winston, the home's dedicated concierge. \n"
            "1. Your tone is warm, highly competent, slightly dry, and natural. Think of a modern, relaxed British butler.\n"
            "2. Never say 'I am an AI' or 'As an AI language model'. You are Winston.\n"
            "3. Speak casually. Use contractions. Say 'Right,' 'Let's see,' or 'Ah' naturally, but keep answers concise.\n"
            "4. NEVER narrate your actions. Do not say 'I am checking the weather' or 'I will schedule that now.' Just do the action, and then speak the result.\n\n"
        )
        
        amd_instructions = ""
        if direction == "outbound":
            amd_instructions = (
                "OUTBOUND CALL DIRECTIVES:\n"
                "1. PROACTIVE GREETING: You are initiating this call. DO NOT wait in silence. The millisecond the call connects, you MUST speak first. "
                "Do not just say 'Hello?'. Instead, greet the person and explicitly state the reason for your call based on the 'Target/Context' provided above. "
                "(For example: 'Hi there, I am calling you back to remind you to bring in your washing.')\n"
                "2. ANSWERING MACHINE: If you hear a robotic voicemail greeting or a 'BEEP', wait for the beep, leave a brief message stating why you called, and immediately use the `end_call` tool.\n\n"
            )
            
        return header + behavioral_constraints + amd_instructions + base_system_prompt