import asyncio
import json
import os
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

STATE_FILE = os.environ.get("STATE_FILE", "/dev/shm/asterisk_ai_state.json")

router = APIRouter()

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
        except Exception:
            pass
        await asyncio.sleep(0.2)

@router.websocket("/")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)