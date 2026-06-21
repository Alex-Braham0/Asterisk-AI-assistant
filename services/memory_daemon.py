import os
import time
import json
import asyncio
import asyncpg
from pydantic import BaseModel
from google import genai
from google.genai import types

class MemoryUpdate(BaseModel):
    reasoning_scratchpad: str
    user_profile_public: str
    user_profile_private: str
    endpoint_profile: str

class DBMemoryDaemon:
    def __init__(self, config, db):
        self.config = config
        self.client = genai.Client(api_key=self.config.gemini_api_key)
        self.db = db

    async def connect(self):
        self.pool = await asyncpg.create_pool(self.db_url, min_size=1, max_size=5)

    def generate_new_profiles(self, pub_user_mem: str, priv_user_mem: str, endpoint_mem: str, call_data: str, device_type: str) -> dict:
        prompt = f"""
You are the intelligence layer managing long-term memory for an AI telephony assistant.
Your objective is to extract facts from the call data and route them into the correct database memory profiles.

CRITICAL DIRECTIVES:
1. PRIORITIZE EXPLICIT REQUESTS: Execute any explicit add/remove requests made by the live agent in the call data.
2. ANTI-AMNESIA: Retain all existing facts from the CURRENT PROFILES unless explicitly told to remove them or if new data directly contradicts them.
3. CONTEXT EFFICIENCY (STRICT LIMIT): You MUST keep each profile string under 75 words. Use bullet points or highly condensed telegraphic phrasing. Do not write full paragraphs. The system context window is strictly limited.
4. TAXONOMY ROUTING:
    A. ENDPOINT PROFILE: Facts about the physical hardware, room, or location. (Current Device Type: {device_type})
    B. PUBLIC USER PROFILE: Non-sensitive, permanent facts (Name, language, general preferences).
    C. PRIVATE USER PROFILE: Sensitive facts (Medical, financial, passwords, exact schedules).

CURRENT PUBLIC USER:
{pub_user_mem}

CURRENT PRIVATE USER:
{priv_user_mem}

CURRENT ENDPOINT:
{endpoint_mem}

CALL DATA (Transcript & Live Agent Requests):
{call_data}

INSTRUCTIONS:
First, use the `reasoning_scratchpad` to evaluate the new facts and determine which profile they belong to based on the taxonomy above.
Then, output the fully updated memory strings.
"""
        response = self.client.models.generate_content(
            model='gemini-3.1-flash-lite',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=MemoryUpdate,
                temperature=0.1
            ),
        )
        return json.loads(response.text)

    async def process_task(self, task_id: int, payload: str):
        data = json.loads(payload)
        extension = data.get("extension")
        user_id = data.get("user_id")

        key_exchanges = data.get("key_exchanges", [])
        proposed_updates = data.get("proposed_memory_updates", [])
        
        if not key_exchanges and not proposed_updates:
            await self._mark_task_done(task_id, 'skipped')
            return

        call_data_payload = json.dumps({
            "explicit_agent_requests": proposed_updates,
            "transcript": key_exchanges
        }, indent=2)

        # Fetch current memory from Markdown files
        ep_file = f"./memory_files/endpoints/{extension}.md"
        ep_mem = "No specific memory data."
        if os.path.exists(ep_file):
            with open(ep_file, "r", encoding="utf-8") as f:
                ep_mem = f.read()

        pub_mem = "No existing memory profile."
        priv_mem = "No existing memory profile."
        if user_id:
            pub_file = f"./memory_files/users/{user_id}_public.md"
            priv_file = f"./memory_files/users/{user_id}_private.md"
            if os.path.exists(pub_file):
                with open(pub_file, "r", encoding="utf-8") as f:
                    pub_mem = f.read()
            if os.path.exists(priv_file):
                with open(priv_file, "r", encoding="utf-8") as f:
                    priv_mem = f.read()

        # Call LLM (Run in thread to avoid blocking asyncio loop)
        device_type = "UNKNOWN" 
        async with self.db.pool.acquire() as conn:
            device_type_val = await conn.fetchval("SELECT device_type FROM Endpoints WHERE extension = $1", str(extension))
            if device_type_val: device_type = device_type_val
            
        new_profiles = await asyncio.to_thread(
            self.generate_new_profiles, pub_mem, priv_mem, ep_mem, call_data_payload, device_type
        )

        # Write new memories back to Markdown files
        os.makedirs("./memory_files/endpoints", exist_ok=True)
        new_ep_mem = new_profiles.get("endpoint_profile", "").strip()
        if new_ep_mem and new_ep_mem != ep_mem and new_ep_mem.lower() not in ["", "none", "[]"]:
            with open(ep_file, "w", encoding="utf-8") as f:
                f.write(new_ep_mem)

        if user_id:
            os.makedirs("./memory_files/users", exist_ok=True)
            new_pub = new_profiles.get("user_profile_public", "").strip()
            new_priv = new_profiles.get("user_profile_private", "").strip()
            
            if new_pub and new_pub != pub_mem and new_pub.lower() not in ["", "none", "[]"]:
                with open(pub_file, "w", encoding="utf-8") as f:
                    f.write(new_pub)
            if new_priv and new_priv != priv_mem and new_priv.lower() not in ["", "none", "[]"]:
                with open(priv_file, "w", encoding="utf-8") as f:
                    f.write(new_priv)

        await self._mark_task_done(task_id, 'completed')
        print(f"[MemoryDaemon] Successfully processed memory update for Ext {extension} to MD files.")

    async def _mark_task_done(self, task_id: int, status: str):
        async with self.db.pool.acquire() as conn:
            await conn.execute("UPDATE Tasks SET status = $1 WHERE id = $2", status, task_id)

    async def run(self):
        print("[Memory Daemon] Connected to DB. Scanning for synthesis tasks...")
        while True:
            try:
                # Lock a pending task
                query = """
                    UPDATE Tasks SET status = 'processing' 
                    WHERE id = (
                        SELECT id FROM Tasks WHERE task_type = 'memory_synthesis' AND status = 'pending' 
                        ORDER BY id ASC LIMIT 1 FOR UPDATE SKIP LOCKED
                    ) RETURNING id, payload;
                """
                async with self.db.pool.acquire() as conn:
                    task = await conn.fetchrow(query)
                
                if task:
                    await self.process_task(task['id'], task['payload'])
                else:
                    await asyncio.sleep(3)
            except Exception as e:
                print(f"[Memory Daemon Error] {e}")
                await asyncio.sleep(5)

if __name__ == "__main__":
    daemon = DBMemoryDaemon()
    asyncio.run(daemon.run())