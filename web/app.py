import asyncio
import json
import os
import sqlite3
import logging
from contextlib import asynccontextmanager, closing
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

DB_PATH = os.environ.get("DB_PATH", "/opt/asterisk-ai/data/call_history.db")
STATE_FILE = os.environ.get("STATE_FILE", "/dev/shm/asterisk_ai_state.json")

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r") as f:
                    state = json.load(f)
                await websocket.send_json({"event": "state_change", "data": state})
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections.copy():
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()

async def poll_state_file():
    last_mtime = 0
    while True:
        try:
            if os.path.exists(STATE_FILE):
                current_mtime = os.path.getmtime(STATE_FILE)
                if current_mtime != last_mtime:
                    with open(STATE_FILE, "r") as f:
                        state_data = f.read()
                    parsed_data = json.loads(state_data)
                    await manager.broadcast({"event": "state_change", "data": parsed_data})
                    last_mtime = current_mtime
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        except Exception as e:
            logging.error(f"Polling error: {e}")
            
        await asyncio.sleep(0.2)

@asynccontextmanager
async def lifespan(app: FastAPI):
    poller_task = asyncio.create_task(poll_state_file())
    yield
    poller_task.cancel()

app = FastAPI(lifespan=lifespan)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Asterisk AI Dashboard</title>
    <style>
        body { font-family: sans-serif; max-width: 800px; margin: 2rem auto; }
        .status-in_call { color: red; font-weight: bold; }
        .status-idle { color: green; font-weight: bold; }
    </style>
</head>
<body>
    <h1>System Status: <span id="status">Connecting...</span></h1>
    <p>Current Caller: <span id="caller">-</span></p>
    
    <h2>Recent Calls</h2>
    <button onclick="loadHistory()">Refresh History</button>
    <ul id="history"></ul>

    <script>
        const ws = new WebSocket(`ws://${location.host}/ws`);
        ws.onmessage = function(event) {
            const msg = JSON.parse(event.data);
            if (msg.event === "state_change") {
                const statusEl = document.getElementById("status");
                statusEl.innerText = msg.data.status;
                statusEl.className = `status-${msg.data.status}`;
                document.getElementById("caller").innerText = msg.data.caller_id || "-";
                
                if (msg.data.status === "idle") {
                    setTimeout(loadHistory, 500); // Slight delay to ensure DB write finishes
                }
            }
        };

        async function loadHistory() {
            try {
                const res = await fetch('/api/history');
                const data = await res.json();
                const list = document.getElementById("history");
                list.innerHTML = "";
                data.forEach(call => {
                    list.innerHTML += `<li>${call.start_time} | Caller: ${call.caller_id} | ${call.duration}s</li>`;
                });
            } catch (err) {
                console.error("Failed to load history", err);
            }
        }
        loadHistory();
    </script>
</body>
</html>
"""

@app.get("/")
async def get():
    # async avoids thread-pool overhead for pure memory returns
    return HTMLResponse(HTML_TEMPLATE)

@app.get("/api/history")
def get_history():
    # Sync def offloads blocking DB I/O to a background thread
    try:
        with closing(sqlite3.connect(DB_PATH, timeout=5.0)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM history ORDER BY id DESC LIMIT 50").fetchall()
            return [dict(row) for row in rows]
    except sqlite3.OperationalError as e:
        logging.warning(f"History read failed: {e}")
        return []

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)