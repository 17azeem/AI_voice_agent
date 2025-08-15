from pydantic import BaseModel
from typing import List

class ChatResponse(BaseModel):
    session_id: str
    transcript: str
    llm_response: str
    audio_urls: List[str]
