import logging
import uuid
from fastapi import FastAPI , WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.core.config import STATIC_DIR

from .core.logging_config import setup_logging
from .routers.chat import router as chat_router

setup_logging()

app = FastAPI(title="Voice Agent")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def serve_index():
    return FileResponse(f"{STATIC_DIR}/index.html")

# Routers
app.include_router(chat_router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()   # receive message from client
        await websocket.send_text(f"Echo: {data}")  # send back

@app.websocket("/ws/audio")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    filename = f"recorded_audio_{uuid.uuid4().hex}.webm"
    print(f"Saving audio to {filename}")
    
    try:
        with open(filename, "wb") as f:
            while True:
                try:
                    data = await websocket.receive_bytes()
                    f.write(data)
                except WebSocketDisconnect:
                    print("Client disconnected gracefully")
                    break
                except Exception as e:
                    print("Unexpected error:", e)
                    break
    finally:
        print("WebSocket session ended")