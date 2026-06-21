import os
import sys
import subprocess
import atexit

APP_PID = os.getpid()
TX_SINK = f"Baresip_Tx_{APP_PID}"
RX_SINK = f"Baresip_Rx_{APP_PID}"

print(f"[System] Provisioning hardware virtual cables for PID {APP_PID}...")

# 1. Purge any orphaned sinks from previous crashes
subprocess.run(f"pactl list short modules | grep Baresip_ | cut -f1 | xargs -r pactl unload-module", shell=True, stderr=subprocess.DEVNULL)

# 2. Force OS-level creation of the PulseAudio sinks
subprocess.run(["pactl", "load-module", "module-null-sink", f"sink_name={TX_SINK}", f"sink_properties=device.description={TX_SINK}"], check=True, stdout=subprocess.DEVNULL)
subprocess.run(["pactl", "load-module", "module-null-sink", f"sink_name={RX_SINK}", f"sink_properties=device.description={RX_SINK}"], check=True, stdout=subprocess.DEVNULL)

# 3. Bind Python's global C-libraries to the new cables
os.environ["PULSE_SINK"] = TX_SINK
os.environ["PULSE_SOURCE"] = f"{RX_SINK}.monitor"

# 4. Guarantee cleanup on exit or systemd SIGTERM
def cleanup_cables():
    subprocess.run(f"pactl list short modules | grep {APP_PID} | cut -f1 | xargs -r pactl unload-module", shell=True, stderr=subprocess.DEVNULL)

atexit.register(cleanup_cables)

import asyncio
from config.settings import AppSettings
from config.logging_config import setup_logging
from db.connection import DatabaseConnection
from core.orchestrator import SIPAgentOrchestrator
from services.pbx_sync import PBXSynchronizer

async def init_system(config: AppSettings, db: DatabaseConnection):
    print("[System] Initializing Database Connections...")
    await db.connect()
    
    print("[System] Synchronizing FreePBX Directory Data...")
    synchronizer = PBXSynchronizer(config, db.pool)
    await synchronizer.run_sync()

def main():
    setup_logging(level="INFO")
    
    try:
        config = AppSettings.load_or_create("config.json")
    except Exception as e:
        print(f"[FATAL] Configuration error: {e}")
        sys.exit(1)

    db = DatabaseConnection(config.postgres_url)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Run async setup tasks prior to booting the media engine loop
    loop.run_until_complete(init_system(config, db))
    
    # Initialize the core task scheduler and RTP handler
    orchestrator = SIPAgentOrchestrator(config, db, loop)
    orchestrator.start()

if __name__ == "__main__":
    main()