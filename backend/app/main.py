import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from app.core.config import STATIC_DIR
from app.services.stt_service import STTService


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

@app.websocket("/ws/audio")
async def audio_ws(websocket: WebSocket):
    await websocket.accept()
    print("🎙️ Client connected")

    file_path = os.path.join(OUTPUT_DIR, "recorded_audio.webm")

    # Remove previous recording if exists
    if os.path.exists(file_path):
        os.remove(file_path)

    try:
        with open(file_path, "ab") as f:
            while True:
                data = await websocket.receive_bytes()
                if data == b"END":
                    break

                # Write raw bytes to file
                f.write(data)

    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        print(f"⚠️ Error in WS: {e}")
    finally:
        # Close file
        print(f"✅ Audio saved at {file_path}")

        # Transcribe using STTService
        text = stt_service.transcribe_file(file_path)
        print(f"📝 Transcription:\n{text}")

        await websocket.close()