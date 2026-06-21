import os
import json
import asyncio
import logging
from aiohttp import web, WSMsgType
from collections import deque
from pathlib import Path
from datetime import datetime

# Global ring buffer for recent logs and active websockets
log_ring_buffer = deque(maxlen=500)
active_websockets = set()

class DashboardLogHandler(logging.Handler):
    """Intercepts system logs and broadcasts them, ignoring dashboard noise."""
    def emit(self, record):
        # Drop web server access logs and dashboard polling
        if record.name == 'aiohttp.access':
            return
        if "GET /api/status" in record.getMessage() or "GET /api/logs" in record.getMessage():
            return
            
        msg = self.format(record)
        log_ring_buffer.append(msg)
        for ws in list(active_websockets):
            if not ws.closed:
                asyncio.create_task(ws.send_str(msg))
            else:
                active_websockets.discard(ws)

class DashboardServer:
    def __init__(self, orchestrator, db):
        self.orchestrator = orchestrator
        self.db = db
        self.base_dir = Path(__file__).resolve().parent.parent

    async def init_app(self):
        app = web.Application()
        app.router.add_get('/api/status', self.get_status)
        app.router.add_get('/api/logs/ws', self.websocket_handler)
        app.router.add_get('/api/calls', self.get_past_calls)
        app.router.add_get('/api/missions', self.get_missions)
        app.router.add_put('/api/missions/{id}', self.update_mission)
        
        # Updated Memory Routes
        app.router.add_get('/api/memory/{type}', self.list_memory)
        app.router.add_get('/api/memory/{type}/{item_id}', self.get_memory)
        app.router.add_put('/api/memory/{type}/{item_id}', self.update_memory)

        static_dir = os.path.join(os.path.dirname(__file__), 'static')
        os.makedirs(static_dir, exist_ok=True)
        
        async def index_handler(request):
            return web.FileResponse(os.path.join(static_dir, 'index.html'))
        
        app.router.add_get('/', index_handler)
        return app

    async def get_status(self, request):
        is_locked = self.orchestrator.line_lock.locked()
        active_call = self.orchestrator.engine.active_call
        
        call_info = None
        if active_call:
            call_info = {
                "id": active_call._id,
                "caller_name": active_call.request.headers['From']['caller'],
                "caller_number": active_call.request.headers['From']['number']
            }

        return web.json_response({
            "line_active": is_locked,
            "call_info": call_info
        })

    async def websocket_handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        # Send history first
        for log in log_ring_buffer:
            await ws.send_str(log)
            
        active_websockets.add(ws)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            active_websockets.discard(ws)
        return ws

    async def get_past_calls(self, request):
        processed_dir = self.base_dir / "call_summaries/processed"
        calls = []
        if processed_dir.exists():
            for file_path in processed_dir.glob("*.json"):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        calls.append({
                            "filename": file_path.name,
                            "summary": data.get("detailed_transcript_summary", "No summary available."),
                            "duration": data.get("duration_seconds", 0),
                            "timestamp": data.get("start_time", "Unknown"),
                            "extension": data.get("extension", "Unknown")
                        })
                except Exception:
                    pass
        # Sort newest first
        calls.sort(key=lambda x: x["timestamp"], reverse=True)
        return web.json_response(calls)

    async def get_missions(self, request):
        query = "SELECT id, owner_user_id, run_at_utc, mission_directive, status, final_report FROM Autonomous_Missions ORDER BY run_at_utc DESC"
        async with self.db.pool.acquire() as conn:
            records = await conn.fetch(query)
            
        missions = []
        for r in records:
            missions.append({
                "id": r["id"],
                "owner_id": r["owner_user_id"],
                "run_at_utc": str(r["run_at_utc"]),
                "directive": r["mission_directive"],
                "status": r["status"],
                "final_report": r["final_report"] or ""
            })
        return web.json_response(missions)

    async def update_mission(self, request):
        mission_id = int(request.match_info['id'])
        data = await request.json()
        directive = data.get('directive')
        
        query = "UPDATE Autonomous_Missions SET mission_directive = $1 WHERE id = $2 AND status = 'pending'"
        async with self.db.pool.acquire() as conn:
            result = await conn.execute(query, directive, mission_id)
            
        if result == "UPDATE 0":
            return web.json_response({"error": "Mission not found or already executed."}, status=400)
        return web.json_response({"status": "success"})

    async def list_memory(self, request):
        mem_type = request.match_info['type']
        items = []
        
        async with self.db.pool.acquire() as conn:
            if mem_type == "endpoints":
                records = await conn.fetch("SELECT extension, display_name FROM Endpoints ORDER BY extension")
                for r in records:
                    items.append({
                        "id": str(r["extension"]), 
                        "display": f"{r['extension']} - {r['display_name']}"
                    })
            elif mem_type == "users":
                records = await conn.fetch("SELECT id, primary_name FROM Users ORDER BY primary_name")
                for r in records:
                    # Map to the _public and _private markdown files generated by the memory daemon
                    items.append({
                        "id": f"{r['id']}_public", 
                        "display": f"{r['primary_name']} (Public Profile)"
                    })
                    items.append({
                        "id": f"{r['id']}_private", 
                        "display": f"{r['primary_name']} (Private Profile)"
                    })
                    
        return web.json_response(items)

    async def get_memory(self, request):
        mem_type = request.match_info['type']
        item_id = request.match_info['item_id']
        file_path = self.base_dir / f"memory_files/{mem_type}/{item_id}.md"
        
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                return web.json_response({"content": f.read()})
        return web.json_response({"content": ""})

    async def update_memory(self, request):
        mem_type = request.match_info['type']
        item_id = request.match_info['item_id']
        data = await request.json()
        
        target_dir = self.base_dir / f"memory_files/{mem_type}"
        target_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = target_dir / f"{item_id}.md"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(data.get("content", ""))
            
        return web.json_response({"status": "success"})

async def start_dashboard(orchestrator, db, host="0.0.0.0", port=8080):
    server = DashboardServer(orchestrator, db)
    app = await server.init_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(f"[Dashboard] Online at http://{host}:{port}")