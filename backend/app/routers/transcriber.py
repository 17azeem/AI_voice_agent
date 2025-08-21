import os
import asyncio
import json
import base64
import websockets
from fastapi import WebSocket
from assemblyai.streaming.v3 import (
    StreamingClient, StreamingClientOptions,
    StreamingParameters, StreamingSessionParameters,
    StreamingEvents, BeginEvent, TurnEvent,
    TerminationEvent, StreamingError
)
from app.services.llm_service import LLMService, types

llm_service = LLMService()
aai_api_key = os.getenv("ASSEMBLYAI_API_KEY")
MURF_WS_URL = os.getenv("MURF_TTS_WS")
MURF_API_KEY = os.getenv("MURF_API_KEY")

class AssemblyAIStreamingTranscriber:
    def __init__(self, websocket: WebSocket, loop, sample_rate=16000):
        self.websocket = websocket
        self.loop = loop
        self.murf_ws = None  # Murf websocket connection

        # AssemblyAI streaming client
        self.client = StreamingClient(
            StreamingClientOptions(api_key=aai_api_key)
        )
        self.client.on(StreamingEvents.Begin, self.on_begin)
        self.client.on(StreamingEvents.Turn, self.on_turn)
        self.client.on(StreamingEvents.Termination, self.on_termination)
        self.client.on(StreamingEvents.Error, self.on_error)

        self.client.connect(
            StreamingParameters(sample_rate=sample_rate, format_turns=False)
        )

    def on_begin(self, client, event: BeginEvent):
        print(f"🎤 Session started: {event.id}")

    def on_turn(self, client, event: TurnEvent):
        print(f"User transcript: {event.transcript} (end_of_turn={event.end_of_turn})")
        if event.end_of_turn and event.transcript.strip():
            # Send transcript to client
            asyncio.run_coroutine_threadsafe(
                self.websocket.send_json({"type": "transcript", "text": event.transcript}),
                self.loop
            )
            # Stream LLM → Murf
            asyncio.run_coroutine_threadsafe(
                self.stream_llm_to_murf(event.transcript),
                self.loop
            )

            if not event.turn_is_formatted:
                client.set_params(StreamingSessionParameters(format_turns=True))

    async def stream_llm_to_murf(self, user_text: str):
        """Stream LLM response to Murf TTS and print base64 audio"""
        try:
            if not MURF_WS_URL or not MURF_API_KEY:
                print("❌ Murf WS URL or API key missing!")
                return

            murf_url = f"{MURF_WS_URL}?api-key={MURF_API_KEY}&sample_rate=44100&channel_type=MONO&format=WAV"
            print(f"🌐 Connecting to Murf WS: {murf_url}")

            if not self.murf_ws or not getattr(self.murf_ws, "open", False):
                print("🌐 (Re)connecting to Murf WS...")
                self.murf_ws = await websockets.connect(murf_url)
                print("🎤 Murf WS connected")
                voice_config_msg = {
                    "voice_config": {
                        "voiceId": "en-US-amara",
                        "style": "Conversational",
                        "rate": 0,
                        "pitch": 0,
                        "variation": 1
                    }
                }
                await self.murf_ws.send(json.dumps(voice_config_msg))
                print("✅ Voice config sent to Murf")

            full_audio_base64 = []

            async def receive_audio():
                first_chunk = True
                try:
                    while True:
                        msg = await self.murf_ws.recv()
                        data = json.loads(msg)

                        if "audio" in data:
                            audio_b64 = data["audio"]
                            full_audio_base64.append(audio_b64)
                            print(f"🔊 Murf audio chunk (base64 length={len(audio_b64)})")
                            print(audio_b64[:100] + "..." if len(audio_b64) > 100 else audio_b64)

                        if data.get("final"):
                            print("✅ Murf TTS completed for this turn")
                            combined_b64 = "".join(full_audio_base64)
                            print(f"🎧 Full audio base64 length: {len(combined_b64)}")
                            break
                except Exception as e:
                    print("❌ Error receiving Murf audio:", e)

            async def send_llm_to_murf():
                try:
                    history = [types.Content(role="user", parts=[types.Part(text=user_text)])]
                    for chunk in llm_service.stream(history):
                        print("💬 LLM chunk:", chunk)
                        await self.murf_ws.send(json.dumps({"text": chunk, "end": False}))
                    # send final end=True after all chunks
                    await self.murf_ws.send(json.dumps({"text": "", "end": True}))
                except Exception as e:
                    print("❌ Error streaming LLM response:", e)

            await asyncio.gather(receive_audio(), send_llm_to_murf())
            print("✅ Ready for next user transcript")

        except Exception as e:
            print("❌ Error in stream_llm_to_murf:", e)

    def stream_audio(self, audio_chunk: bytes):
        try:
            self.client.stream(audio_chunk)
        except Exception as e:
            print("❌ Error sending audio to AssemblyAI:", e)

    def on_termination(self, client, event: TerminationEvent):
        print(f"🛑 Session terminated after {event.audio_duration_seconds} s")

    def on_error(self, client, error: StreamingError):
        print("❌ Streaming error:", error)

    async def close_murf(self):
        if self.murf_ws:
            await self.murf_ws.close()
            self.murf_ws = None
            print("🛑 Murf WS closed")

    def close(self):
        self.client.disconnect(terminate=True)
        print("🛑 AssemblyAI client disconnected")
