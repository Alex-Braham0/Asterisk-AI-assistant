import os
import time
import json
import shutil
from pathlib import Path
from google import genai

# Load configuration
try:
    with open("config.json", "r") as f:
        config_data = json.load(f)
except FileNotFoundError:
    print("[Memory Manager] FATAL: config.json not found.")
    exit(1)

# Initialize the modern GenAI Client
client = genai.Client(api_key=config_data.get("gemini_api_key"))

# Define Paths
PENDING_DIR = Path("./call_summaries/pending")
PROCESSED_DIR = Path("./call_summaries/processed")
MEMORY_DIR = Path("./memory_files")

# Ensure directories exist
PENDING_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

MAX_CHARS = 2000

def get_existing_memory(extension: str) -> str:
    memory_file = MEMORY_DIR / f"{extension}.md"
    if memory_file.exists():
        with open(memory_file, "r") as f:
            return f.read()
    return "No existing memory profile."

def write_memory(extension: str, content: str):
    memory_file = MEMORY_DIR / f"{extension}.md"
    with open(memory_file, "w") as f:
        f.write(content.strip())

def process_memory_file(file_path: Path):
    print(f"\n[Memory Manager] Processing: {file_path.name}")
    try:
        with open(file_path, "r") as f:
            summary_data = json.load(f)
            
        extension = summary_data.get("extension")
        if not extension:
            raise ValueError("No extension found in summary JSON.")

        updates = summary_data.get("proposed_memory_updates", [])
        details = summary_data.get("detailed_transcript_summary", "")
        
        if not updates and not details:
            print(f"[Memory Manager] No actionable data for {file_path.name}. Archiving.")
            shutil.move(str(file_path), str(PROCESSED_DIR / file_path.name))
            return

        current_memory = get_existing_memory(extension)

        # The System Prompt configuring the AI's behavior
        system_instruction = f"""
You are the memory manager for an AI phone agent. Your job is to maintain a rolling Markdown profile of the user.
You are the sole decision-maker on what to keep, what to prune, and how to format it.

RULES:
1. STRICT LIMIT: The final output MUST be under {MAX_CHARS} characters. 
2. RUTHLESS PRUNING: If adding new facts exceeds the limit, you must delete older, trivial facts (e.g., small talk, old moods) to make room.
3. CRITICAL DATA SURVIVES: Never delete critical health (e.g., allergies), emergency contacts, or core identity facts.
4. FORMATTING: Output ONLY the raw Markdown text. No conversational filler, no codeblocks (do not wrap in ```markdown).

CURRENT MEMORY PROFILE:
{current_memory}

NEW DATA TO INTEGRATE:
Proposed Updates: {json.dumps(updates)}
Call Context: {details}

Rewrite the entire memory profile now.
"""
        
        # Start a chat session so we can reply if it fails the length check
        chat = client.chats.create(model="gemini-2.5-flash")
        
        print("[Memory Manager] Sending to LLM for integration...")
        response = chat.send_message(system_instruction)
        new_profile = response.text.strip()
        
        # The Validation Loop (Max 3 retries)
        retries = 0
        while len(new_profile) > MAX_CHARS and retries < 3:
            overshoot = len(new_profile) - MAX_CHARS
            print(f"[Memory Manager] Warning: Output is {len(new_profile)} chars (Over by {overshoot}). Forcing rewrite...")
            
            correction_prompt = f"Your previous output was {len(new_profile)} characters. This is a hard failure. You must rewrite the profile to be strictly under {MAX_CHARS} characters. Delete less important information immediately."
            
            response = chat.send_message(correction_prompt)
            new_profile = response.text.strip()
            retries += 1
            
        if len(new_profile) > MAX_CHARS:
            print(f"[Memory Manager] ERROR: AI failed to compress below {MAX_CHARS} after 3 attempts. Aborting update to protect system latency.")
            error_file = PENDING_DIR / f"{file_path.name}.length_error"
            os.rename(file_path, error_file)
            return

        # Success: Write the file and archive the JSON
        write_memory(extension, new_profile)
        shutil.move(str(file_path), str(PROCESSED_DIR / file_path.name))
        
        print(f"[Memory Manager] Success! Profile updated ({len(new_profile)}/{MAX_CHARS} chars).")
        
    except Exception as e:
        print(f"[Memory Manager] CRITICAL ERROR processing {file_path.name}: {e}")
        error_file = PENDING_DIR / f"{file_path.name}.error"
        os.rename(file_path, error_file)

def start_daemon():
    print("[Memory Manager] Daemon initialized. Booting directory scanner...")
    while True:
        for file_path in PENDING_DIR.glob("*.json"):
            process_memory_file(file_path)
        time.sleep(5)

if __name__ == "__main__":
    start_daemon()