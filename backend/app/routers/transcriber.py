import os
import asyncio
import assemblyai as aai
from fastapi import WebSocket
from assemblyai.streaming.v3 import (
    StreamingClient, StreamingClientOptions,
    StreamingParameters, StreamingSessionParameters,
    StreamingEvents, BeginEvent, TurnEvent,
    TerminationEvent, StreamingError
)
from app.services.llm_service import LLMService, types, SYSTEM_PROMPT
llm_service = LLMService()
aai_api_key = os.getenv("ASSEMBLYAI_API_KEY")

class AssemblyAIStreamingTranscriber:
    def __init__(self, websocket: WebSocket, loop, sample_rate=16000):
        self.websocket = websocket
        self.loop = loop  # main FastAPI event loop

        self.client = StreamingClient(
            StreamingClientOptions(
                api_key=aai_api_key,
                api_host="streaming.assemblyai.com"
            )
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
        print(f"{event.transcript} (end_of_turn={event.end_of_turn})")

        if event.end_of_turn and event.transcript.strip():
            try:
                # Send transcript to client
                asyncio.run_coroutine_threadsafe(
                    self.websocket.send_json({
                        "type": "transcript",
                        "text": event.transcript
                    }),
                    self.loop
                )

                # Schedule LLM streaming properly
                asyncio.run_coroutine_threadsafe(
                    self.stream_ai_response(event.transcript),
                    self.loop
                )

            except Exception as e:
                print("⚠️ Failed in on_turn:", e)

            if not event.turn_is_formatted:
                client.set_params(
                    StreamingSessionParameters(format_turns=True)
                )

    async def stream_ai_response(self, user_text: str):
        """Stream LLM response, print in console, and send to websocket"""
        llm = llm_service
        loop = asyncio.get_running_loop()

        def gen_chunks():
            history = [types.Content(role="user", parts=[types.Part(text=user_text)])]
            return list(llm.stream(history))

        chunks = await loop.run_in_executor(None, gen_chunks)

        for chunk in chunks:
            print("LLM:", chunk)  # print to console
            await self.websocket.send_json({
                "type": "ai_response",
                "text": chunk
            })

    def on_termination(self, client, event: TerminationEvent):
        print(f"🛑 Session terminated after {event.audio_duration_seconds} s")

    def on_error(self, client, error: StreamingError):
        print("❌ Streaming error:", error)

    def stream_audio(self, audio_chunk: bytes):
        self.client.stream(audio_chunk)

    def close(self):
        self.client.disconnect(terminate=True)
