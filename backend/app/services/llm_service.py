import logging
from google.generativeai.types import GenerationConfig
from google.generativeai import GenerativeModel
from google.api_core.exceptions import ResourceExhausted
import google.generativeai as genai

logger = logging.getLogger(__name__)

class LLMService:
    def __init__(self, api_key: str, model: str = "gemini-pro"):
        """
        Initializes the LLMService with a specific API key.

        Args:
            api_key (str): The Gemini API key.
            model (str): The Gemini model name to use.
        """
        if not api_key:
            raise ValueError("API key for LLMService cannot be None or empty.")
        
        # Configure the API key globally. This is the correct way.
        genai.configure(api_key=api_key)
        
        self.model = model
        # Initialize the GenerativeModel client without the api_key argument
        self.client = GenerativeModel(
            model_name="models/gemini-1.5-flash-latest",
            generation_config=GenerationConfig(temperature=0.5)
        )
        self.system_prompt = "You are a helpful AI assistant. Answer concisely and accurately."

    def stream(self, history: list) -> str:
        """
        Streams a response from the Gemini model based on chat history.
        Yields tokens one by one.

        Args:
            history (list): A list of chat messages in the expected format.
        
        Yields:
            str: A chunk of text from the streamed response.
        """
        contents = [{
            "role": "user",
            "parts": [{"text": self.system_prompt}]
        }] + history[-8:]
        
        try:
            stream = self.client.generate_content(
                contents=contents,
                stream=True,
            )
            for chunk in stream:
                if hasattr(chunk, 'text'):
                    yield chunk.text
        except ResourceExhausted:
            logger.warning("Resource Exhausted. Retrying after a short break.")
            yield "Sorry, there's been an issue. Please try again in a few moments."
        except Exception as e:
            logger.error("LLM streaming error: %s", e, exc_info=True)
            yield "I ran into a problem. Can you rephrase that?"