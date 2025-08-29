import logging
import asyncio
import websockets
import json
import base64
from typing import Optional
from ..core.config import MURF_API_KEY

logger = logging.getLogger(__name__)

WS_URL = "wss://api.murf.ai/v1/speech/stream-input"


class TTSService:
    def __init__(self, voice_id: str = "en-US-amara") -> None:
        self.voice_id = voice_id

    async def synthesize_stream(self, text: str) -> None:
        """
        Stream text to Murf WebSocket TTS API.
        Print the received base64 audio to the console.
        """
        try:
            async with websockets.connect(
                f"{WS_URL}?api-key={MURF_API_KEY}&sample_rate=44100&channel_type=MONO&format=WAV"
            ) as ws:
                # Configure the voice
                voice_config_msg = {
                    "voice_config": {
                        "voiceId": self.voice_id,
                        "style": "Conversational",
                        "rate": 0,
                        "pitch": 0,
                        "variation": 1
                    }
                }
                await ws.send(json.dumps(voice_config_msg))
                logger.info(f"Sent voice config: {voice_config_msg}")

                # Send the text
                text_msg = {"text": text, "end": True}
                await ws.send(json.dumps(text_msg))
                logger.info(f"Sent text: {text[:50]}...")

                first_chunk = True
                while True:
                    response = await ws.recv()
                    data = json.loads(response)

                    if "audio" in data:
                        audio_b64 = data["audio"]
                        logger.info(f"🔊 Received audio chunk (base64, len={len(audio_b64)})")
                        print(audio_b64)  # <-- Print the base64 audio to console

                        # If you want raw bytes instead:
                        audio_bytes = base64.b64decode(audio_b64)
                        if first_chunk and len(audio_bytes) > 44:  # skip WAV header
                            audio_bytes = audio_bytes[44:]
                            first_chunk = False

                    if data.get("final"):
                        logger.info("✅ TTS streaming complete")
                        break

        except Exception as e:
            logger.exception("❌ Murf WebSocket error: %s", e)
