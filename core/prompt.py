import json
from zoneinfo import ZoneInfo
from datetime import datetime
from typing import Callable, List, Dict, Any

class PromptManager:
    def __init__(self, template_path: str = "config.json"):
        self.template_path = template_path
        # Modular registry of context providers
        self._context_providers: List[Callable[[Dict[str, Any]], Dict[str, str]]] = [
            self._uk_time_provider,
            self._caller_identity_provider
        ]

    def register_provider(self, provider_func: Callable[[Dict[str, Any]], Dict[str, str]]) -> None:
        """Register additional dynamic context providers in the future (e.g. CRM data)."""
        self._context_providers.append(provider_func)

    def _uk_time_provider(self, context: dict) -> dict:
        """Provider: Calculates exact UK time (handles GMT/BST automatically)."""
        now_uk = datetime.now(ZoneInfo("Europe/London"))
        return {
            "Current UK Time": now_uk.strftime("%I:%M %p (%Z) on %A, %B %d, %Y")
        }

    def _caller_identity_provider(self, context: dict) -> dict:
        """Provider: Extracts caller name and extension details."""
        return {
            "Caller Name": context.get("caller_name", "Unknown"),
            "Extension / Number": context.get("caller_number", "Unknown")
        }

    def build_prompt(self, context: dict) -> str:
        """Loads base JSON system prompt and appends all dynamic live context blocks."""
        try:
            with open(self.template_path, "r") as f:
                base_prompt = json.load(f).get("system_prompt", "")
        except Exception as e:
            print(f"[PromptManager] ⚠️ Failed to load template from {self.template_path}: {e}")
            base_prompt = "You are a voice assistant connected to a SIP phone system."

        live_context_blocks = []
        for provider in self._context_providers:
            try:
                data = provider(context)
                for key, val in data.items():
                    live_context_blocks.append(f"- {key}: {val}")
            except Exception as e:
                print(f"[PromptManager] ⚠️ Context provider error: {e}")

        if live_context_blocks:
            context_section = "\n\n# LIVE CALL CONTEXT\n" + "\n".join(live_context_blocks)
            return base_prompt + context_section

        return base_prompt