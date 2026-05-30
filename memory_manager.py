import os
import time
import json
import shutil
from pathlib import Path
from google import genai

try:
    with open("config.json", "r") as f:
        config_data = json.load(f)
except FileNotFoundError:
    print("[Memory Manager] FATAL: config.json not found.")
    exit(1)

client = genai.Client(api_key=config_data.get("gemini_api_key"))

PENDING_DIR = Path("./call_summaries/pending")
PROCESSED_DIR = Path("./call_summaries/processed")
MEMORY_USERS_DIR = Path("./memory_files/users")
MEMORY_ENDPOINTS_DIR = Path("./memory_files/endpoints")

for d in [PENDING_DIR, PROCESSED_DIR, MEMORY_USERS_DIR, MEMORY_ENDPOINTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

MAX_CHARS = 2000

def get_existing_memory(target_dir: Path, filename: str) -> str:
    memory_file = target_dir / f"{filename}.md"
    if memory_file.exists():
        with open(memory_file, "r") as f:
            return f.read()
    return "No existing memory profile."

def write_memory(target_dir: Path, filename: str, content: str):
    memory_file = target_dir / f"{filename}.md"
    with open(memory_file, "w") as f:
        f.write(content.strip())

def process_memory_file(file_path: Path):
    print(f"\n[Memory Manager] Processing: {file_path.name}")
    try:
        with open(file_path, "r") as f:
            summary_data = json.load(f)
            
        entity_type = summary_data.get("target_entity_type", "endpoint")
        entity_id = summary_data.get("target_entity_id", summary_data.get("extension"))
        
        if not entity_id:
            raise ValueError("No target entity ID found in summary JSON.")

        updates = summary_data.get("proposed_memory_updates", [])
        details = summary_data.get("detailed_transcript_summary", "")
        
        if not updates and not details:
            print(f"[Memory Manager] No actionable data. Archiving.")
            shutil.move(str(file_path), str(PROCESSED_DIR / file_path.name))
            return

        target_dir = MEMORY_USERS_DIR if entity_type == "user" else MEMORY_ENDPOINTS_DIR
        current_memory = get_existing_memory(target_dir, entity_id)

        system_instruction = f"""
You are the memory manager for an AI phone agent. Your job is to maintain a rolling Markdown profile of the user or physical hardware endpoint.
You are the sole decision-maker on what to keep, what to prune, and how to format it.

RULES:
1. STRICT LIMIT: The final output MUST be under {MAX_CHARS} characters. 
2. RUTHLESS PRUNING: Delete older, trivial facts to make room for new ones.
3. FORMATTING: Output ONLY the raw Markdown text. No conversational filler, no codeblocks (do not wrap in ```markdown).

CURRENT MEMORY PROFILE:
{current_memory}

NEW DATA TO INTEGRATE:
Proposed Updates: {json.dumps(updates)}
Call Context: {details}

Rewrite the entire memory profile now.
"""
        
        chat = client.chats.create(model="gemini-2.5-flash")
        response = chat.send_message(system_instruction)
        new_profile = response.text.strip()
        
        if len(new_profile) > MAX_CHARS:
            response = chat.send_message(f"HARD FAILURE. Your output was {len(new_profile)} chars. Compress below {MAX_CHARS} immediately.")
            new_profile = response.text.strip()
            
        if len(new_profile) > MAX_CHARS:
            print(f"[Memory Manager] ERROR: AI failed to compress. Aborting.")
            os.rename(file_path, PENDING_DIR / f"{file_path.name}.length_error")
            return

        write_memory(target_dir, entity_id, new_profile)
        shutil.move(str(file_path), str(PROCESSED_DIR / file_path.name))
        print(f"[Memory Manager] Success! {entity_type}/{entity_id} updated.")
        
    except Exception as e:
        print(f"[Memory Manager] CRITICAL ERROR processing {file_path.name}: {e}")
        os.rename(file_path, PENDING_DIR / f"{file_path.name}.error")

def start_daemon():
    print("[Memory Manager] Daemon initialized. Booting directory scanner...")
    while True:
        for file_path in PENDING_DIR.glob("*.json"):
            process_memory_file(file_path)
        time.sleep(5)

if __name__ == "__main__":
    start_daemon()