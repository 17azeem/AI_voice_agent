import logging
import assemblyai as aai
from ..core.config import ASSEMBLYAI_API_KEY

logger = logging.getLogger(__name__)

class STTService:
    def __init__(self) -> None:
        aai.settings.api_key = ASSEMBLYAI_API_KEY
        logger.debug("AssemblyAI API key configured")

    def transcribe_file(self, file_path: str) -> str:
        logger.info("Starting transcription")
        transcriber = aai.Transcriber()
        transcript_object = transcriber.transcribe(file_path)
        text = (transcript_object.text or "").strip()
        if not text:
            logger.warning("Empty transcript received from STT")
            return "[Could not transcribe audio]"
        logger.info("Transcription complete")
        return text
