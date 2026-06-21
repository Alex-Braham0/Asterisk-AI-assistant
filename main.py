import os
import sys

os.environ["PULSE_SINK"] = "Baresip_Tx"
os.environ["PULSE_SOURCE"] = "Baresip_Rx.monitor"

import asyncio
from config.settings import AppSettings
from config.logging_config import setup_logging
from db.connection import DatabaseConnection
from core.orchestrator import SIPAgentOrchestrator
from services.pbx_sync import PBXSynchronizer
from dashboard.server import start_dashboard

async def init_system(config: AppSettings, db: DatabaseConnection, orchestrator: SIPAgentOrchestrator):
    print("[System] Initializing Database Connections...")
    await db.connect()
    
    print("[System] Synchronizing FreePBX Directory Data...")
    synchronizer = PBXSynchronizer(config, db.pool)
    await synchronizer.run_sync()

    asyncio.create_task(start_dashboard(orchestrator, db))

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
    
    orchestrator = SIPAgentOrchestrator(config, db, loop)

    # Run async setup tasks prior to booting the media engine loop
    loop.run_until_complete(init_system(config, db, orchestrator))
    
    orchestrator.start()

if __name__ == "__main__":
    main()