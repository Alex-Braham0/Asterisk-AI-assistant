import os
import sys
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from web.routers import calls, live

@asynccontextmanager
async def lifespan(app: FastAPI):
    poller_task = asyncio.create_task(live.poll_state_file())
    yield
    poller_task.cancel()

app = FastAPI(lifespan=lifespan)

# Register the modular routers
app.include_router(calls.router, prefix="/api/calls", tags=["calls"])
app.include_router(live.router, prefix="/ws", tags=["live"])

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Asterisk AI Dashboard</title>
    <style>
        body { font-family: sans-serif; max-width: 800px; margin: 2rem auto; }
        .status-in_call { color: red; font-weight: bold; }
        .status-idle { color: green; font-weight: bold; }
        .metadata-pill { background: #eee; padding: 2px 6px; border-radius: 4px; font-size: 0.8em; margin-left: 10px; }
    </style>
</head>
<body>
    <h1>System Status: <span id="status">Connecting...</span></h1>
    <p>Current Caller: <span id="caller">-</span></p>
    
    <h2>Recent Calls</h2>
    <button onclick="loadHistory()">Refresh History</button>
    <ul id="history"></ul>

    <script>
        const ws = new WebSocket(`ws://${location.host}/ws/`);
        ws.onmessage = function(event) {
            const msg = JSON.parse(event.data);
            if (msg.event === "state_change") {
                const statusEl = document.getElementById("status");
                statusEl.innerText = msg.data.status;
                statusEl.className = `status-${msg.data.status}`;
                document.getElementById("caller").innerText = msg.data.caller_id || "-";
                
                if (msg.data.status === "idle") {
                    setTimeout(loadHistory, 500); 
                }
            }
        };

        async function loadHistory() {
            try {
                const res = await fetch('/api/calls/'); // <--- Note the updated path!
                const data = await res.json();
                const list = document.getElementById("history");
                list.innerHTML = "";
                data.forEach(call => {
                    // Compute duration safely in the frontend
                    let start = new Date(call.start_time);
                    let end = call.end_time ? new Date(call.end_time) : start;
                    let duration = Math.round((end - start) / 1000);
                    
                    list.innerHTML += `
                        <li>
                            <strong>${call.start_time}</strong> | ${call.direction.toUpperCase()} | Remote: ${call.remote_identity} | ${duration}s
                        </li>`;
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
    return HTMLResponse(HTML_TEMPLATE)