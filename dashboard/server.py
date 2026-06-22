import os
import sys
import json
import asyncio
import logging
import datetime
from aiohttp import web, WSMsgType
from collections import deque
from pathlib import Path

log_ring_buffer = deque(maxlen=500)
active_websockets = set()
_original_stdout = sys.stdout

# Intercepts ALL terminal prints and routes them to the dashboard
class DashboardStdoutCapture:
    def write(self, text):
        _original_stdout.write(text)
        if text.strip() and "GET /api/" not in text:
            msg = text.strip()
            log_ring_buffer.append(msg)
            try:
                loop = asyncio.get_running_loop()
                for ws in list(active_websockets):
                    if not ws.closed:
                        loop.create_task(ws.send_str(msg))
            except RuntimeError:
                pass # Event loop not running yet

    def flush(self):
        _original_stdout.flush()

sys.stdout = DashboardStdoutCapture()

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
        
        # Memory Routes
        app.router.add_get('/api/memory/{type}/{item_id}', self.get_memory)
        app.router.add_put('/api/memory/{type}/{item_id}', self.update_memory)
        
        # Diagnostics
        app.router.add_get('/api/system/health', self.get_system_health)
        app.router.add_post('/api/system/recover', self.recover_missions)
        app.router.add_post('/api/system/restart_telephony', self.restart_telephony)
        app.router.add_post('/api/missions/preview', self.preview_mission)
        
        # User & Endpoint Management Routes
        app.router.add_get('/api/system/users', self.get_users_list)
        app.router.add_post('/api/system/users', self.create_user)
        app.router.add_delete('/api/system/users/{user_id}', self.delete_user)
        
        app.router.add_get('/api/system/endpoints', self.get_endpoints_list)
        app.router.add_put('/api/system/endpoints/{extension}', self.update_endpoint)
        
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
        
        # Dump the history immediately upon UI connection
        for msg in log_ring_buffer:
            await ws.send_str(msg)
            
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

    async def get_memory(self, request):
        file_path = self.base_dir / f"memory_files/{request.match_info['type']}/{request.match_info['item_id']}.md"
        content = file_path.read_text() if file_path.exists() else ""
        return web.json_response(
            {"content": content},
            # Force the browser to NEVER cache this API response
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"} 
        )

    async def update_memory(self, request):
        mem_type = request.match_info['type']
        item_id = request.match_info['item_id']
        data = await request.json()
        new_content = data.get("content", "")
        
        async with self.db.pool.acquire() as conn:
            if mem_type == 'users':
                user_id, visibility = item_id.split('_')
                column = "public_memory" if visibility == "public" else "private_memory"
                await conn.execute(f"UPDATE Users SET {column} = $1 WHERE id = $2", new_content, int(user_id))
            elif mem_type == 'endpoints':
                await conn.execute("UPDATE Endpoints SET endpoint_memory = $1 WHERE extension = $2", new_content, item_id)
                
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
            
        query = "SELECT id, primary_name as name, public_memory, private_memory FROM Users WHERE id = $1"
        user_data = None
        async with self.db.pool.acquire() as conn:
            user_record = await conn.fetchrow(query, int(owner_id))
            if user_record:
                user_data = dict(user_record)
                
        builder = ContextBuilder(self.orchestrator.config)
        prompt = builder.build_headless_instruction(
            mission_directive=directive, 
            user_data=user_data
        )
        
        return web.json_response({"prompt": prompt})

    # --- User & Endpoint Management Routes ---
    async def get_users_list(self, request):
        async with self.db.pool.acquire() as conn:
            records = await conn.fetch("SELECT id, primary_name FROM Users ORDER BY primary_name")
            return web.json_response([dict(r) for r in records])

    async def create_user(self, request):
        data = await request.json()
        name = data.get('primary_name', '').strip()
        if not name: return web.json_response({"error": "Name required"}, status=400)
        async with self.db.pool.acquire() as conn:
            new_id = await conn.fetchval("INSERT INTO Users (primary_name) VALUES ($1) RETURNING id", name)
        return web.json_response({"status": "success", "id": new_id})

    async def delete_user(self, request):
        user_id = int(request.match_info['user_id'])
        async with self.db.pool.acquire() as conn:
            # Postgres CASCADE drops Endpoint_Users & orphaned missions based on schema
            await conn.execute("DELETE FROM Users WHERE id = $1", user_id)
        
        # Clean up memory files
        try:
            pub_file = self.base_dir / f"memory_files/users/{user_id}_public.md"
            priv_file = self.base_dir / f"memory_files/users/{user_id}_private.md"
            # if pub_file.exists(): os.remove(pub_file)
            # if priv_file.exists(): os.remove(priv_file)
        except Exception: pass
            
        return web.json_response({"status": "success"})

    async def get_endpoints_list(self, request):
        async with self.db.pool.acquire() as conn:
            records = await conn.fetch("SELECT extension, display_name, device_type FROM Endpoints ORDER BY extension")
            return web.json_response([dict(r) for r in records])

    async def update_endpoint(self, request):
        ext = request.match_info['extension']
        data = await request.json()
        async with self.db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE Endpoints SET display_name = $1, device_type = $2 WHERE extension = $3", 
                data['display_name'], data['device_type'], ext
            )
        return web.json_response({"status": "success"})

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
                await conn.execute("DELETE FROM Endpoint_Users WHERE user_id = $1", user_id)
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