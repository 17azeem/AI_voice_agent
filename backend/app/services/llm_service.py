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

    def stream(self, history: list[types.Content]):
        """
        Stream response from Gemini LLM.
        Yields tokens one by one.
        """
        contents = [SYSTEM_PROMPT] + history[-8:]
        logger.info("Streaming LLM response with %d messages", len(contents))

        try:
            stream = self.client.models.generate_content_stream(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(max_output_tokens=1000, temperature=0.3),
            )
            for event in stream:
                if hasattr(event, "candidates") and event.candidates:
                    for candidate in event.candidates:
                        if hasattr(candidate, "content") and candidate.content.parts:
                            for part in candidate.content.parts:
                                if hasattr(part, "text") and part.text:
                                    yield part.text
        except Exception as e:
            logger.exception("Error while streaming response: %s", e)
            yield "[Error generating response]"
