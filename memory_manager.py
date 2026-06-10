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

class MemoryManager:
    def __init__(self, api_key: str, base_dir: str = ".", max_chars: int = 2000):
        self.client = genai.Client(api_key=api_key)
        self.base_dir = Path(base_dir)
        
        self.pending_dir = self.base_dir / "call_summaries/pending"
        self.processed_dir = self.base_dir / "call_summaries/processed"
        self.memory_users_dir = self.base_dir / "memory_files/users"
        self.memory_endpoints_dir = self.base_dir / "memory_files/endpoints"
        
        self.max_chars = max_chars
        self._ensure_directories()

    def _ensure_directories(self):
        for d in [self.pending_dir, self.processed_dir, self.memory_users_dir, self.memory_endpoints_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def get_existing_memory(self, target_dir: Path, filename: str) -> str:
        memory_file = target_dir / f"{filename}.md"
        if memory_file.exists():
            with open(memory_file, "r") as f:
                return f.read()
        return "No existing memory profile."

    def write_memory(self, target_dir: Path, filename: str, content: str):
        memory_file = target_dir / f"{filename}.md"
        with open(memory_file, "w") as f:
            f.write(content.strip())

    def generate_new_profiles(self, pub_user_mem: str, priv_user_mem: str, endpoint_mem: str, transcript: str) -> dict:
        prompt = f"""
You are the intelligence layer managing long-term memory for an AI phone agent. 
Your objective is to parse the transcript and update the user and endpoint memory profiles securely.

CRITICAL DIRECTIVES:
1. DATA SCOPING: You MUST split the user's data. 
    - PUBLIC: Trivial facts, generic preferences, names, relationships, general tone preferences.
    - PRIVATE: Calendar events, specific routines, medical/financial context, sensitive notes, passwords.
2. ENDPOINT ISOLATION: Physical traits (e.g. "This phone is in the kitchen") go strictly into the endpoint profile.
3. PRUNING: Discard ephemeral conversational filler. Overwrite obsolete information. Keep word counts dense and highly compressed.

CURRENT PUBLIC USER PROFILE:
{pub_user_mem}

CURRENT PRIVATE USER PROFILE:
{priv_user_mem}

CURRENT ENDPOINT PROFILE:
{endpoint_mem}

CALL TRANSCRIPT:
{transcript}
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
        # Critical change: Defaulting to ID representation. If unknown, leave it as generic unverified string.
        user_id = summary_data.get("user_id") 
        
        if not endpoint_id:
            raise ValueError("No extension/endpoint ID found in summary JSON.")

        transcript = json.dumps(summary_data.get("key_exchanges", []))
        if not transcript or transcript == "[]":
            return "SKIPPED", "No transcript data available to parse."

        current_endpoint_mem = self.get_existing_memory(self.memory_endpoints_dir, endpoint_id)
        
        pub_user_mem = "No existing memory profile."
        priv_user_mem = "No existing memory profile."
        
        if user_id:
            pub_user_mem = self.get_existing_memory(self.memory_users_dir, f"{user_id}_public")
            priv_user_mem = self.get_existing_memory(self.memory_users_dir, f"{user_id}_private")

        new_profiles = self.generate_new_profiles(pub_user_mem, priv_user_mem, current_endpoint_mem, transcript)
        
        updated_entities = []
        
        # Endpoint Memory
        endpoint_profile = new_profiles.get("endpoint_profile", "").strip()
        if endpoint_profile and endpoint_profile != "No existing memory profile.":
            self.write_memory(self.memory_endpoints_dir, endpoint_id, endpoint_profile)
            updated_entities.append(f"Endpoint({endpoint_id})")

        # User Memory (Only if verified user_id was passed by the active session)
        if user_id:
            pub_profile = new_profiles.get("user_profile_public", "").strip()
            priv_profile = new_profiles.get("user_profile_private", "").strip()
            
            if pub_profile and pub_profile != "No existing memory profile.":
                self.write_memory(self.memory_users_dir, f"{user_id}_public", pub_profile)
                updated_entities.append(f"UserPublic({user_id})")
                
            if priv_profile and priv_profile != "No existing memory profile.":
                self.write_memory(self.memory_users_dir, f"{user_id}_private", priv_profile)
                updated_entities.append(f"UserPrivate({user_id})")

        if not updated_entities:
            return "SKIPPED", "LLM generated no viable memory outputs."

        return "UPDATED", f"Updated: {', '.join(updated_entities)}."

    def process_memory_file(self, file_path: Path):
        print(f"\n[Memory Manager] Processing: {file_path.name}")
        try:
            with open(file_path, "r") as f:
                summary_data = json.load(f)
                
            status, msg = self.process_summary_dict(summary_data)
            
            if status == "SKIPPED":
                print(f"[Memory Manager] {msg} Archiving without rewrite.")
                shutil.move(str(file_path), str(self.processed_dir / file_path.name))
            elif status == "UPDATED":
                print(f"[Memory Manager] Success! {msg}")
                shutil.move(str(file_path), str(self.processed_dir / file_path.name))
                
        except ValueError as e:
            print(f"[Memory Manager] VALUE ERROR processing {file_path.name}: {e}")
            os.rename(file_path, self.pending_dir / f"{file_path.name}.error")
        except Exception as e:
            print(f"[Memory Manager] CRITICAL ERROR processing {file_path.name}: {e}")
            os.rename(file_path, self.pending_dir / f"{file_path.name}.error")

    def start_daemon(self, poll_interval: int = 5):
        print("[Memory Manager] Daemon initialized. Booting directory scanner...")
        while True:
            for file_path in self.pending_dir.glob("*.json"):
                self.process_memory_file(file_path)
            time.sleep(poll_interval)


if __name__ == "__main__":
    try:
        with open("config.json", "r") as f:
            config_data = json.load(f)
    except FileNotFoundError:
        print("[Memory Manager] FATAL: config.json not found.")
        exit(1)

    api_key = config_data.get("gemini_api_key")
    if not api_key:
        print("[Memory Manager] FATAL: gemini_api_key not found in config.json.")
        exit(1)

    manager = MemoryManager(api_key=api_key)
    manager.start_daemon()