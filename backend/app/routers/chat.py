import logging
import os
import tempfile
import uuid
from typing import Dict, List

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from google.genai import types

from ..schemas.chat import ChatResponse
from ..services.stt_service import STTService
from ..services.tts_service import TTSService
from ..services.llm_service import LLMService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])

# In-memory chat sessions (swap to Redis/db if needed)
chat_sessions: Dict[str, List[types.Content]] = {}

stt = STTService()
tts = TTSService()
llm = LLMService()

@router.post("/chat/{session_id}", response_model=ChatResponse)
async def chat_agent(session_id: str, audio: UploadFile = File(...)):
    if session_id == "new" or not session_id:
        session_id = str(uuid.uuid4())

    if not audio:
        raise HTTPException(status_code=400, detail="No audio file uploaded")

    audio_file_path = None
    try:
        # Save uploaded audio to temp
        ext = os.path.splitext(audio.filename or "")[-1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(await audio.read())
            audio_file_path = tmp.name
        logger.info("Saved uploaded audio to %s", audio_file_path)

        # STT
        user_text = stt.transcribe_file(audio_file_path)

        # Session
        history = chat_sessions.setdefault(session_id, [])
        history.append(types.Content(role="user", parts=[types.Part(text=user_text)]))

        # LLM
        reply_text = llm.generate(history)

        # Append reply
        history.append(types.Content(role="model", parts=[types.Part(text=reply_text)]))

        # TTS
        audio_urls = tts.synthesize(reply_text)

        logger.debug("Transcript: %s", user_text)
        logger.debug("LLM reply: %s", reply_text)

        return ChatResponse(
            session_id=session_id,
            transcript=user_text,
            llm_response=reply_text,
            audio_urls=audio_urls,
        )
    finally:
        if audio_file_path and os.path.exists(audio_file_path):
            try:
                os.remove(audio_file_path)
            except Exception as e:
                logger.warning("Failed to delete temp file %s: %s", audio_file_path, e)
