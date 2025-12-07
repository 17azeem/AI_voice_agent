import logging
from google.generativeai.types import GenerationConfig
from google.generativeai import GenerativeModel
from google.api_core.exceptions import ResourceExhausted
import google.generativeai as genai
from typing import Generator

logger = logging.getLogger(__name__)

class LLMService:
    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        if not api_key:
            raise ValueError("API key for LLMService cannot be None or empty.")

        genai.configure(api_key=api_key)

        self.client = GenerativeModel(
            model_name=model,
            generation_config=GenerationConfig(temperature=0.5)
        )

        self.system_prompt = "You are a helpful AI assistant. Answer concisely and accurately."

    def stream(self, history: list) -> Generator[str, None, None]:
        messages = [
            {
                "role": "system",
                "parts": [{"text": self.system_prompt}],
            }
        ] + history[-8:]  # keep last 8 messages

        try:
            response_stream = self.client.generate_content(
                contents=messages,
                stream=True
            )

            for chunk in response_stream:
                try:
                    # Extract the valid generated text
                    text = (
                        chunk.candidates[0]
                            .content.parts[0]
                            .text
                    )
                    if text:
                        yield text

                except Exception:
                    # Some chunks may not have text — skip silently
                    continue

        except ResourceExhausted:
            logger.warning("Resource exhausted error.")
            yield "Sorry, resources exceeded. Try again later."

        except Exception as e:
            logger.error("LLM streaming error: %s", e, exc_info=True)
            yield "I ran into a problem. Can you rephrase that?"
