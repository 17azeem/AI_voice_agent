import os
import asyncio
import json
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
MURF_WS_URL = os.getenv("MURF_TTS_WS", "wss://api.murf.ai/v1/speech/stream-input")
MURF_API_KEY = os.getenv("MURF_API_KEY")

class AssemblyAIStreamingTranscriber:
    def __init__(self, websocket: WebSocket, loop, sample_rate=16000):
        self.websocket = websocket
        self.loop = loop
        self.murf_ws = None  # Murf websocket connection
        self.chat_history: list[types.Content] = []  # <-- keep conversational memory

        # AssemblyAI streaming client
        self.client = StreamingClient(StreamingClientOptions(api_key=aai_api_key))
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
            # Stream LLM → Murf (+ stream AI text to client)
            asyncio.run_coroutine_threadsafe(
                self.stream_llm_to_murf(event.transcript),
                self.loop
            )

            if not event.turn_is_formatted:
                client.set_params(StreamingSessionParameters(format_turns=True))

    async def stream_llm_to_murf(self, user_text: str):
        """
        For a single turn:
        1) Build history (with memory) and stream LLM text both to
           the Murf TTS WS and to the frontend as `llm_text`.
        2) Receive Murf audio, forward as ordered `ai_audio` chunks (chunk_id starts at 1 per turn).
        3) On completion, send `llm_text_final` and `ai_audio` with {"final": True}.
        4) Append (user, model) to chat history.
        """
        try:
            if not MURF_API_KEY:
                print("❌ Murf API key missing!")
                return

            murf_url = f"{MURF_WS_URL}?api-key={MURF_API_KEY}&sample_rate=44100&channel_type=MONO&format=WAV"
            print(f"🌐 Murf WS URL: {murf_url}")

            # Connect once and reuse
            if not self.murf_ws or not getattr(self.murf_ws, "open", False):
                self.murf_ws = await websockets.connect(murf_url)
                print("🎤 Murf WS connected")

                # Configure voice once per connection
                voice_config_msg = {
                    "voice_config": {
                        "voiceId": "en-IN-eashwar",
                        "style": "Conversational",
                        "rate": 0,
                        "pitch": 0,
                        "variation": 1
                    }
                }
                await self.murf_ws.send(json.dumps(voice_config_msg))
                print("✅ Voice config sent to Murf")

            # --- Per-turn sequential chunk IDs start at 1
            turn_chunk_id = 1

            async def receive_audio():
                """Forward Murf audio to frontend in order for THIS turn."""
                nonlocal turn_chunk_id
                full_audio_base64 = []
                try:
                    while True:
                        msg = await self.murf_ws.recv()
                        data = json.loads(msg)

                        if "audio" in data:
                            audio_b64 = data["audio"]
                            full_audio_base64.append(audio_b64)

                            await self.websocket.send_json({
                                "type": "ai_audio",
                                "chunk_id": turn_chunk_id,
                                "audio": audio_b64
                            })
                            print(f"🔊 Sent Murf audio chunk #{turn_chunk_id} (len={len(audio_b64)})")
                            turn_chunk_id += 1

                        if data.get("final"):
                            # notify frontend playback is complete for this turn
                            await self.websocket.send_json({
                                "type": "ai_audio",
                                "final": True
                            })
                            print("✅ Murf TTS completed for this turn")
                            break
                except Exception as e:
                    print("❌ Error receiving Murf audio:", e)

            async def send_llm_to_murf_and_client():
                """Stream LLM text to Murf and to the client; send final text at end; update memory."""
                full_text = []
                try:
                    # Build LLM input with memory
                    history_for_llm = self.chat_history + [
                        types.Content(role="user", parts=[types.Part(text=user_text)])
                    ]

                    # Stream chunks
                    for chunk in llm_service.stream(history_for_llm):
                        if not chunk:
                            continue
                        full_text.append(chunk)

                        # Send to TTS (streaming)
                        await self.murf_ws.send(json.dumps({"text": chunk, "end": False}))

                        # Send to frontend as streaming text
                        await self.websocket.send_json({"type": "llm_text", "text": chunk})

                    # End of text stream to Murf
                    await self.murf_ws.send(json.dumps({"text": "", "end": True}))

                    # Finalize text to client
                    final_text = "".join(full_text).strip()
                    await self.websocket.send_json({"type": "llm_text_final", "text": final_text})

                    # Update memory
                    self.chat_history.append(
                        types.Content(role="user", parts=[types.Part(text=user_text)])
                    )
                    self.chat_history.append(
                        types.Content(role="model", parts=[types.Part(text=final_text)])
                    )
                except Exception as e:
                    print("❌ Error streaming LLM/Murf:", e)
                    try:
                        await self.websocket.send_json({"type": "llm_text_final", "text": "[Error generating response]"})
                    except Exception:
                        pass

            # Run both tasks concurrently
            await asyncio.gather(receive_audio(), send_llm_to_murf_and_client())
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
