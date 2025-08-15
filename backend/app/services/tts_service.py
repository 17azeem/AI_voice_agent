import logging
import requests
from typing import Optional, List
from ..core.config import MURF_API_KEY

logger = logging.getLogger(__name__)

class TTSService:
    def __init__(self, voice_id: str = "en-IN-eashwar") -> None:
        self.voice_id = voice_id
        self.url = "https://api.murf.ai/v1/speech/generate"
        self.headers = {
            "accept": "application/json",
            "api-key": MURF_API_KEY,
            "Content-Type": "application/json",
        }

    def synthesize(self, text: str, chunk_size: int = 3000) -> List[str]:
        audio_urls: List[str] = []
        logger.info("Starting TTS synthesis")
        for i in range(0, len(text), chunk_size):
            chunk = text[i : i + chunk_size]
            url = self._generate_audio(chunk)
            if url:
                audio_urls.append(url)
        logger.info("TTS synthesis complete")
        return audio_urls

    def _generate_audio(self, text: str) -> Optional[str]:
        payload = {"voiceId": self.voice_id, "text": text}
        resp = requests.post(self.url, json=payload, headers=self.headers, timeout=60)
        try:
            resp.raise_for_status()
        except Exception as e:
            logger.exception("Murf TTS error: %s | body=%s", e, resp.text[:500])
            return None
        data = resp.json()
        logger.debug("Murf response: %s", data)
        return data.get("audioFile")
