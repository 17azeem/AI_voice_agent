import logging
from google.generativeai.types import GenerationConfig
from google.generativeai import GenerativeModel
from google.api_core.exceptions import ResourceExhausted
import google.generativeai as genai
from typing import Generator, Any

logger = logging.getLogger(__name__)

class LLMService:
    def __init__(self, api_key: str, model: str = "gemini-pro"):
        if not api_key:
            raise ValueError("API key for LLMService cannot be None or empty.")
        
        genai.configure(api_key=api_key)
        self.model = model
        self.client = GenerativeModel(
            model_name="models/gemini-1.5-flash-latest",
            generation_config=GenerationConfig(temperature=0.5)
        )
        self.system_prompt = "You are a helpful AI assistant. Answer concisely and accurately."

    # FIX: This method must be a synchronous generator.
    def stream(self, history: list) -> Generator[str, None, None]:
        contents = [{
            "role": "user",
            "parts": [{"text": self.system_prompt}]
        }] + history[-8:]
        
        try:
            # The underlying library returns a synchronous generator here.
            stream = self.client.generate_content(
                contents=contents,
                stream=True,
            )
            # FIX: Use a simple 'for' loop to iterate over the synchronous stream.
            for chunk in stream:
                if hasattr(chunk, 'text'):
                    yield chunk.text
        except ResourceExhausted:
            logger.warning("Resource Exhausted. Retrying after a short break.")
            yield "Sorry, there's been an issue. Please try again in a few moments."
        except Exception as e:
            logger.error("LLM streaming error: %s", e, exc_info=True)
            yield "I ran into a problem. Can you rephrase that?"