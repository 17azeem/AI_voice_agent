import os
import asyncio
import tempfile
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from app.core.config import STATIC_DIR
from app.services.stt_service import STTService
from app.services.llm_service import LLMService
from app.routers.transcriber import AssemblyAIStreamingTranscriber

# === Load environment variables ===
load_dotenv()
OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

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

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def get_index():
    return FileResponse("static/index.html")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("🎤 Client connected")

    loop = asyncio.get_running_loop()
    transcriber = AssemblyAIStreamingTranscriber(websocket, loop)

    try:
        # Step 1: Receive the config message first (JSON)
        # This is the crucial change to get the API keys from the frontend
        config_data = await websocket.receive_json()
        if config_data.get("type") == "config":
            print("🔑 Received config message. Initializing services...")
            await transcriber.initialize_services(config_data)
        else:
            await websocket.close(code=1008, reason="First message must be config.")
            return

        # Step 2: Handle incoming audio data (bytes)
        # Now we enter the loop to receive the audio stream
        while True:
            data = await websocket.receive_bytes()
            if transcriber.client:
                transcriber.stream_audio(data)
            else:
                print("⚠️ AAI client not initialized. Cannot stream audio.")
                await websocket.send_json({"type": "error", "text": "AssemblyAI client not ready."})

    except WebSocketDisconnect:
        print("🔌 WebSocket connection disconnected.")
    except Exception as e:
        print(f"❌ An error occurred: {e}")
    finally:
        print("🧹 Cleaning up transcriber resources.")
        transcriber.close()
        await transcriber.close_murf()