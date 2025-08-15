import logging
from google import genai
from google.genai import types
from ..core.config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = types.Content(
    role="user",
    parts=[types.Part(text="You are a helpful AI assistant. Answer concisely and accurately.")]
)

class LLMService:
    def __init__(self, model: str = "models/gemini-2.5-flash"):
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.model = model

    def generate(self, history: list[types.Content]) -> str:
        contents = [SYSTEM_PROMPT] + history[-8:]
        logger.info("Calling LLM with %d messages", len(contents))
        resp = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(max_output_tokens=1000, temperature=0.3),
        )
        text = "I'm having trouble generating a response right now."
        try:
            cand0 = resp.candidates[0]
            part0 = cand0.content.parts[0]
            if hasattr(part0, "text") and part0.text:
                text = part0.text
        except Exception as e:
            logger.exception("Failed extracting LLM text: %s", e)
        return text
