import os
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from app.core.config import STATIC_DIR
from app.services.stt_service import STTService
from app.routers.transcriber import AssemblyAIStreamingTranscriber


# === Load environment variables ===
load_dotenv()
OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

stt_service = STTService()
# === FastAPI setup ===
app = FastAPI(title="Voice Agent")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def serve_index():
    return FileResponse(f"{STATIC_DIR}/index.html")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("🎤 Client connected")

    loop = asyncio.get_running_loop()
    transcriber = AssemblyAIStreamingTranscriber(
        websocket, loop, sample_rate=16000)

    try:
        while True:
            data = await websocket.receive_bytes()
            transcriber.stream_audio(data)

    except Exception as e:
        print(f"⚠️ WebSocket connection closed: {e}")

    finally:
        transcriber.close()