import os
import json
import asyncio
import logging
import datetime
from aiohttp import web, WSMsgType
from collections import deque
from pathlib import Path

log_ring_buffer = deque(maxlen=500)
active_websockets = set()

class DashboardLogHandler(logging.Handler):
    def emit(self, record):
        if record.name == 'aiohttp.access' or "GET /api/status" in record.getMessage() or "GET /api/system/health" in record.getMessage():
            return
        msg = self.format(record)
        log_ring_buffer.append(msg)
        for ws in list(active_websockets):
            if not ws.closed:
                asyncio.create_task(ws.send_str(msg))

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
        app.router.add_get('/api/memory/{type}', self.list_memory)
        app.router.add_get('/api/memory/{type}/{item_id}', self.get_memory)
        app.router.add_put('/api/memory/{type}/{item_id}', self.update_memory)
        
        app.router.add_get('/api/system/health', self.get_system_health)
        app.router.add_post('/api/system/recover', self.recover_missions)
        app.router.add_post('/api/system/restart_telephony', self.restart_telephony)
        app.router.add_post('/api/missions/preview', self.preview_mission)
        
        # New Routing Editor Endpoints
        app.router.add_get('/api/system/users', self.get_users_list)
        app.router.add_get('/api/system/endpoints', self.get_endpoints_list)
        app.router.add_get('/api/system/assignments/{user_id}', self.get_user_assignments)
        app.router.add_post('/api/system/assignments/{user_id}', self.update_user_assignments)

        static_dir = os.path.join(os.path.dirname(__file__), 'static')
        async def index_handler(request):
            return web.FileResponse(os.path.join(static_dir, 'index.html'))
        app.router.add_get('/', index_handler)
        app.router.add_static('/static', static_dir)
        return app

    async def get_status(self, request):
        return web.json_response({
            "line_active": self.orchestrator.line_lock.locked(),
            "call_info": {
                "caller_name": self.orchestrator.engine.active_call.request.headers['From']['caller'],
                "caller_number": self.orchestrator.engine.active_call.request.headers['From']['number']
            } if self.orchestrator.engine.active_call else None
        })

    async def websocket_handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        active_websockets.add(ws)
        try:
            async for msg in ws: pass
        finally:
            active_websockets.discard(ws)
        return ws

    async def get_past_calls(self, request):
        processed_dir = self.base_dir / "call_summaries/processed"
        calls = []
        if processed_dir.exists():
            for f in processed_dir.glob("*.json"):
                try:
                    with open(f, "r") as file:
                        data = json.load(file)
                        calls.append({
                            "filename": f.name,
                            "summary": data.get("detailed_transcript_summary", ""),
                            "duration": data.get("duration_seconds", 0),
                            "timestamp": data.get("start_time", "Unknown"),
                            "extension": data.get("extension", "Unknown")
                        })
                except: pass
        return web.json_response(sorted(calls, key=lambda x: x["timestamp"], reverse=True))

    async def get_missions(self, request):
        async with self.db.pool.acquire() as conn:
            records = await conn.fetch("SELECT * FROM Autonomous_Missions ORDER BY run_at_utc DESC")
            results = []
            for r in records:
                d = dict(r)
                # Safely convert all datetime objects (like run_at_utc and created_at) to strings
                for k, v in d.items():
                    if isinstance(v, datetime.datetime):
                        d[k] = str(v)
                results.append(d)
            return web.json_response(results)

    async def update_mission(self, request):
        m_id = int(request.match_info['id'])
        data = await request.json()
        async with self.db.pool.acquire() as conn:
            await conn.execute("UPDATE Autonomous_Missions SET mission_directive = $1 WHERE id = $2", data['directive'], m_id)
        return web.json_response({"status": "success"})

    async def list_memory(self, request):
        mem_type = request.match_info['type']
        items = []
        async with self.db.pool.acquire() as conn:
            if mem_type == "endpoints":
                records = await conn.fetch("SELECT extension, display_name FROM Endpoints ORDER BY extension")
                for r in records:
                    items.append({"id": str(r["extension"]), "display": f"{r['extension']} - {r['display_name']}"})
            elif mem_type == "users":
                records = await conn.fetch("SELECT id, primary_name FROM Users ORDER BY primary_name")
                for r in records:
                    items.append({"id": f"{r['id']}_public", "display": f"{r['primary_name']} (Public)"})
                    items.append({"id": f"{r['id']}_private", "display": f"{r['primary_name']} (Private)"})
        return web.json_response(items)

    async def get_memory(self, request):
        file_path = self.base_dir / f"memory_files/{request.match_info['type']}/{request.match_info['item_id']}.md"
        content = file_path.read_text() if file_path.exists() else ""
        return web.json_response({"content": content})

    async def update_memory(self, request):
        file_path = self.base_dir / f"memory_files/{request.match_info['type']}/{request.match_info['item_id']}.md"
        data = await request.json()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(data.get("content", ""))
        return web.json_response({"status": "success"})

    async def get_system_health(self, request):
        st = os.statvfs(self.base_dir)
        total_gb = (st.f_blocks * st.f_frsize) / (1024 ** 3)
        free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
        
        pool_size = self.db.pool.get_size()
        pool_idle = self.db.pool.get_idle_size()
        
        engine = self.orchestrator.engine
        baresip_running = engine.baresip_process is not None and engine.baresip_process.poll() is None
        
        return web.json_response({
            "disk": {"total": round(total_gb, 1), "used": round(total_gb - free_gb, 1)},
            "db": {"size": pool_size, "active": pool_size - pool_idle, "idle": pool_idle},
            "engine": {"running": baresip_running, "queue_size": engine.pbx_to_ai_queue.qsize()}
        })

    async def recover_missions(self, request):
        count = await self.db.missions.recover_orphaned_missions()
        return web.json_response({"recovered": count})

    async def restart_telephony(self, request):
        engine = self.orchestrator.engine
        def blocking_restart():
            engine.stop()
            engine.start()
        await asyncio.to_thread(blocking_restart)
        return web.json_response({"status": "success"})

    async def preview_mission(self, request):
        data = await request.json()
        owner_id = data.get('owner_id')
        directive = data.get('directive')
        
        if not owner_id:
            return web.json_response({"prompt": "Error: Missing owner ID."})
            
        user_memory = await self.db.users.get_user_memory(owner_id, access_level="PRIVATE")
        
        prompt = f"""<role_and_identity>
You are a headless, autonomous AI agent executing a background mission. You are NOT currently connected to an audio phone line. You must achieve your objective using your available tools.
</role_and_identity>

<mission_directive>
{directive}
</mission_directive>

<user_context>
You are executing this on behalf of User ID: {owner_id}.
{user_memory}
</user_context>

<strict_directives>
1. TRUST PROVIDED NUMBERS: If your mission explicitly includes a phone number (e.g., "extension 6"), use `execute_outbound_dial` immediately.
2. ASYNCHRONOUS DIALING: The `execute_outbound_dial` tool will return immediately while the phone is ringing. You MUST wait silently. Do not generate any spoken text until you receive the "CALL CONNECTED" system event.
3. THE CONVERSATION: When the call connects, you must converse naturally. Do not end the call immediately. 
4. ENDING THE CALL: Once the conversation reaches a natural conclusion, YOU must invoke the `end_call` tool to hang up the line. 
5. COMPLETING THE MISSION: Only AFTER `end_call` has successfully executed, or if the human hangs up on you (notified via system event), you must invoke the `mark_mission_complete` tool to terminate your background session.
</strict_directives>"""
        return web.json_response({"prompt": prompt})

    # --- Routing Editor Endpoints ---
    async def get_users_list(self, request):
        async with self.db.pool.acquire() as conn:
            records = await conn.fetch("SELECT id, primary_name FROM Users ORDER BY primary_name")
            return web.json_response([dict(r) for r in records])

    async def get_endpoints_list(self, request):
        async with self.db.pool.acquire() as conn:
            records = await conn.fetch("SELECT extension, display_name, device_type FROM Endpoints ORDER BY extension")
            return web.json_response([dict(r) for r in records])

    async def get_user_assignments(self, request):
        user_id = int(request.match_info['user_id'])
        async with self.db.pool.acquire() as conn:
            records = await conn.fetch("SELECT extension, is_default FROM Endpoint_Users WHERE user_id = $1", user_id)
            return web.json_response([dict(r) for r in records])

    async def update_user_assignments(self, request):
        user_id = int(request.match_info['user_id'])
        data = await request.json()
        assignments = data.get('assignments', [])
        
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                # Clear existing relationships for this specific user
                await conn.execute("DELETE FROM Endpoint_Users WHERE user_id = $1", user_id)
                # Insert the new mapped extensions
                for a in assignments:
                    await conn.execute(
                        "INSERT INTO Endpoint_Users (extension, user_id, is_default, access_level) VALUES ($1, $2, $3, 'PRIVATE')",
                        a['extension'], user_id, a['is_default']
                    )
        return web.json_response({"status": "success"})

async def start_dashboard(orchestrator, db, host="0.0.0.0", port=8080):
    server = DashboardServer(orchestrator, db)
    app = await server.init_app()
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()