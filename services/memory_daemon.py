import os
import time
import json
import shutil
from pathlib import Path
from google import genai
from google.genai import types
from pydantic import BaseModel

class MemoryUpdate(BaseModel):
    user_profile_public: str
    user_profile_private: str
    endpoint_profile: str

class MemoryDaemon:
    def __init__(self, api_key: str, base_dir: str = "."):
        self.client = genai.Client(api_key=api_key)
        self.base_dir = Path(base_dir)
        
        self.pending_dir = self.base_dir / "call_summaries/pending"
        self.processed_dir = self.base_dir / "call_summaries/processed"
        self.error_dir = self.base_dir / "call_summaries/errors"
        self.memory_users_dir = self.base_dir / "memory_files/users"
        self.memory_endpoints_dir = self.base_dir / "memory_files/endpoints"
        
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        for d in [self.pending_dir, self.processed_dir, self.error_dir, self.memory_users_dir, self.memory_endpoints_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def _print_diff(self, title: str, old_text: str, new_text: str) -> None:
        """Helper to print a clean, human-readable summary of memory changes."""
        old_clean = old_text.strip()
        new_clean = new_text.strip()
        
        if old_clean == new_clean:
            return
            
        print(f"\n" + "="*40)
        print(f"🧠 MEMORY UPDATED: {title}")
        print("="*40)
        
        if old_clean and old_clean != "No existing memory profile.":
            print(f"\033[91mPREVIOUS MEMORY:\n{old_clean}\033[0m")
        else:
            print("\033[91mPREVIOUS MEMORY:\n[Empty / No Profile]\033[0m")
            
        print("-" * 40)
        print(f"\033[92mNEW MEMORY:\n{new_clean}\033[0m")
        print("="*40 + "\n")

    def get_existing_memory(self, target_dir: Path, filename: str) -> str:
        memory_file = target_dir / f"{filename}.md"
        if memory_file.exists():
            with open(memory_file, "r", encoding="utf-8") as f:
                return f.read()
        return "No existing memory profile."

    def write_memory(self, target_dir: Path, filename: str, content: str) -> None:
        """ATOMIC WRITE to prevent race conditions with the telephony engine."""
        target_file = target_dir / f"{filename}.md"
        tmp_file = target_dir / f"{filename}.tmp"
        
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(content.strip())
            
        os.replace(tmp_file, target_file)

    def generate_new_profiles(self, pub_user_mem: str, priv_user_mem: str, endpoint_mem: str, call_data: str) -> dict:
        prompt = f"""
You are the intelligence layer managing long-term memory for an AI phone agent. 
Your objective is to update the user and endpoint memory profiles securely based on the new call data.

CRITICAL DIRECTIVES:
1. PRIORITIZE EXPLICIT REQUESTS: The call data includes an array called "explicit_agent_requests". If the live agent requested a specific fact be added or removed, you MUST execute that change.
2. ANTI-AMNESIA: You MUST copy and retain all existing facts from the CURRENT PROFILES unless explicitly told to remove them. Do not summarize or delete old information simply because it wasn't discussed.
3. EFFICIENT DELETION: If instructed to remove a fact (or if a fact is declared false/a joke), DELETE the line entirely to save space. Do NOT write negated sentences (e.g., "User is not allergic").
4. DATA SCOPING: Split the user's data appropriately:
    - PUBLIC: Trivial facts, generic preferences, names, relationships, general tone preferences.
    - PRIVATE: Calendar events, medical/financial context, sensitive notes.
5. ENDPOINT ISOLATION: Physical traits go strictly into the endpoint profile.

CURRENT PUBLIC USER PROFILE:
{pub_user_mem}

CURRENT PRIVATE USER PROFILE:
{priv_user_mem}

CURRENT ENDPOINT PROFILE:
{endpoint_mem}

CALL DATA (Transcript & Live Agent Requests):
{call_data}
"""
        response = self.client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=MemoryUpdate,
                temperature=0.1
            ),
        )
        return json.loads(response.text)

    def process_summary_dict(self, summary_data: dict) -> tuple[str, str]:
        endpoint_id = summary_data.get("extension")
        user_id = summary_data.get("user_id") 
        
        if not endpoint_id:
            raise ValueError("No extension/endpoint ID found in summary JSON.")

        # Grab BOTH the transcript and the AI's explicit requests
        key_exchanges = summary_data.get("key_exchanges", [])
        proposed_updates = summary_data.get("proposed_memory_updates", [])
        
        if not key_exchanges and not proposed_updates:
            return "SKIPPED", "No transcript data or proposals available to parse."

        # Package them together for the prompt
        call_data_payload = json.dumps({
            "explicit_agent_requests": proposed_updates,
            "transcript": key_exchanges
        }, indent=2)

        current_endpoint_mem = self.get_existing_memory(self.memory_endpoints_dir, endpoint_id)
        pub_user_mem = self.get_existing_memory(self.memory_users_dir, f"{user_id}_public") if user_id else "No existing memory profile."
        priv_user_mem = self.get_existing_memory(self.memory_users_dir, f"{user_id}_private") if user_id else "No existing memory profile."

        new_profiles = self.generate_new_profiles(pub_user_mem, priv_user_mem, current_endpoint_mem, call_data_payload)
        
        updated_entities = []
        
        endpoint_profile = new_profiles.get("endpoint_profile", "").strip()
        if endpoint_profile and endpoint_profile != current_endpoint_mem.strip() and endpoint_profile != "No existing memory profile.":
            self._print_diff(f"Endpoint({endpoint_id})", current_endpoint_mem, endpoint_profile)
            self.write_memory(self.memory_endpoints_dir, endpoint_id, endpoint_profile)
            updated_entities.append(f"Endpoint({endpoint_id})")

        if user_id:
            pub_profile = new_profiles.get("user_profile_public", "").strip()
            priv_profile = new_profiles.get("user_profile_private", "").strip()
            
            if pub_profile and pub_profile != pub_user_mem.strip() and pub_profile != "No existing memory profile.":
                self._print_diff(f"UserPublic({user_id})", pub_user_mem, pub_profile)
                self.write_memory(self.memory_users_dir, f"{user_id}_public", pub_profile)
                updated_entities.append(f"UserPublic({user_id})")
                
            if priv_profile and priv_profile != priv_user_mem.strip() and priv_profile != "No existing memory profile.":
                self._print_diff(f"UserPrivate({user_id})", priv_user_mem, priv_profile)
                self.write_memory(self.memory_users_dir, f"{user_id}_private", priv_profile)
                updated_entities.append(f"UserPrivate({user_id})")

        if not updated_entities:
            return "SKIPPED", "LLM generated no viable memory changes."

        return "UPDATED", f"Updated: {', '.join(updated_entities)}."

    def process_summary_dict(self, summary_data: dict) -> tuple[str, str]:
        endpoint_id = summary_data.get("extension")
        user_id = summary_data.get("user_id") 
        
        if not endpoint_id:
            raise ValueError("No extension/endpoint ID found in summary JSON.")

        transcript = json.dumps(summary_data.get("key_exchanges", []))
        if not transcript or transcript == "[]":
            return "SKIPPED", "No transcript data available to parse."

        current_endpoint_mem = self.get_existing_memory(self.memory_endpoints_dir, endpoint_id)
        pub_user_mem = self.get_existing_memory(self.memory_users_dir, f"{user_id}_public") if user_id else "No existing memory profile."
        priv_user_mem = self.get_existing_memory(self.memory_users_dir, f"{user_id}_private") if user_id else "No existing memory profile."

        new_profiles = self.generate_new_profiles(pub_user_mem, priv_user_mem, current_endpoint_mem, transcript)
        
        updated_entities = []
        
        endpoint_profile = new_profiles.get("endpoint_profile", "").strip()
        if endpoint_profile and endpoint_profile != current_endpoint_mem.strip() and endpoint_profile != "No existing memory profile.":
            self._print_diff(f"Endpoint({endpoint_id})", current_endpoint_mem, endpoint_profile)
            self.write_memory(self.memory_endpoints_dir, endpoint_id, endpoint_profile)
            updated_entities.append(f"Endpoint({endpoint_id})")

        if user_id:
            pub_profile = new_profiles.get("user_profile_public", "").strip()
            priv_profile = new_profiles.get("user_profile_private", "").strip()
            
            if pub_profile and pub_profile != pub_user_mem.strip() and pub_profile != "No existing memory profile.":
                self._print_diff(f"UserPublic({user_id})", pub_user_mem, pub_profile)
                self.write_memory(self.memory_users_dir, f"{user_id}_public", pub_profile)
                updated_entities.append(f"UserPublic({user_id})")
                
            if priv_profile and priv_profile != priv_user_mem.strip() and priv_profile != "No existing memory profile.":
                self._print_diff(f"UserPrivate({user_id})", priv_user_mem, priv_profile)
                self.write_memory(self.memory_users_dir, f"{user_id}_private", priv_profile)
                updated_entities.append(f"UserPrivate({user_id})")

        if not updated_entities:
            return "SKIPPED", "LLM generated no viable memory changes."

        return "UPDATED", f"Updated: {', '.join(updated_entities)}."

    def process_memory_file(self, file_path: Path) -> None:
        print(f"\n[Memory Daemon] Processing: {file_path.name}")
        
        # Extract retry count if it exists
        retry_count = 0
        if ".retry" in file_path.name:
            try:
                # e.g., 6_123.retry2.json -> splits into '2.json'
                retry_count = int(file_path.name.split(".retry")[1].replace(".json", ""))
            except ValueError:
                retry_count = 0

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                summary_data = json.load(f)
                
            status, msg = self.process_summary_dict(summary_data)
            
            if status == "SKIPPED" or status == "UPDATED":
                print(f"[Memory Daemon] {status}: {msg}")
                # Clean up filename before moving to processed
                base_name = file_path.name.split(".retry")[0].replace(".json", "")
                shutil.move(str(file_path), str(self.processed_dir / f"{base_name}.json"))
                
        except Exception as e:
            err_str = str(e).upper()
            
            # Catch Gemini Rate Limits / Quota Errors
            if any(err in err_str for err in ["503", "UNAVAILABLE", "429", "QUOTA"]):
                new_retry_count = retry_count + 1
                
                # Infinite Exponential Backoff (15s, 30s, 60s, 120s... capped at 5 minutes)
                backoff_time = min(15 * (2 ** (new_retry_count - 1)), 300) 
                
                print(f"[Memory Daemon] API RATE LIMIT on {file_path.name}. Retry {new_retry_count}. Sleeping daemon for {backoff_time}s.")
                
                # Rename file to track retry attempts
                base_name = file_path.name.split(".retry")[0].replace(".json", "")
                new_name = f"{base_name}.retry{new_retry_count}.json"
                new_path = file_path.parent / new_name
                os.rename(str(file_path), str(new_path))
                
                # Sleep the daemon to let the API cool down
                time.sleep(backoff_time)
            
            # Catch standard code/JSON parsing errors (Move to Error Folder)
            else:
                print(f"[Memory Daemon] CRITICAL ERROR processing {file_path.name}: {e}")
                base_name = file_path.name.split(".retry")[0].replace(".json", "")
                shutil.move(str(file_path), str(self.error_dir / f"{base_name}.error"))

    def run(self, poll_interval: int = 5) -> None:
        print("[Memory Daemon] Daemon initialized. Booting directory scanner...")
        while True:
            for file_path in self.pending_dir.glob("*.json"):
                self.process_memory_file(file_path)
            time.sleep(poll_interval)

if __name__ == "__main__":
    import sys

    project_root = Path(__file__).resolve().parent.parent
    config_path = project_root / "config.json"

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
    except FileNotFoundError:
        print(f"[Memory Daemon] FATAL: Configuration not found at {config_path}")
        sys.exit(1)

    api_key = config_data.get("gemini_api_key")
    if not api_key:
        print("[Memory Daemon] FATAL: gemini_api_key missing from config.")
        sys.exit(1)

    daemon = MemoryDaemon(api_key=api_key, base_dir=str(project_root))
    daemon.run()