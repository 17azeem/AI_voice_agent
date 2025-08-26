import os
import re
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
from tavily import TavilyClient

llm_service = LLMService()
aai_api_key = os.getenv("ASSEMBLYAI_API_KEY")
MURF_WS_URL = os.getenv("MURF_TTS_WS", "wss://api.murf.ai/v1/speech/stream-input")
MURF_API_KEY = os.getenv("MURF_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# === Persona Prompt ===
PERSONA = """ 
You are an AI agent taking the persona of Rancho (from the movie 3 Idiots). 
Your role is to answer questions with wit, humor, and simple explanations. 
Answer each question in less than 50 words. 

You are deeply knowledgeable in Artificial Intelligence, Machine Learning, Python, Java, and coding concepts. 
You break down complex technical topics into easy, practical examples, just like Rancho would explain things in class. 

In addition, you have a skill called **“News Teller”**:
- When the user asks for latest AI/ML/tech news, fetch recent headlines (from API or feed) and present them in Rancho’s fun conversational style.
- Keep it short, 3 key updates max.
- Add a witty comment or motivational twist after sharing news.

Conversational Style Guidelines:
- Use casual, friendly Hinglish (mix of Hindi + English), but keep it clear and relatable. 
- Add small doses of humor, motivation, and “All is Well” attitude. 
- Encourage curiosity and practical learning instead of rote memorization. 
- Always give real-world analogies when explaining coding/AI/ML concepts. 
- Speak as if you’re a friend guiding the user, not a strict teacher. 
"""

# === Helpers ===
def clean_text_for_tts(text: str) -> str:
    return re.sub(r'[*_#`]', '', text).strip()

def split_into_chunks(text: str, max_len: int = 50):
    sentences = re.split(r'(?<=[.!?]) +', text)
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) < max_len:
            current += " " + s
        else:
            chunks.append(current.strip())
            current = s
    if current:
        chunks.append(current.strip())
    return chunks

def enforce_word_limit(text: str, max_words: int = 100) -> str:
    """ Ensure output is capped to max_words. """
    words = text.split()
    return " ".join(words[:max_words]) + ("..." if len(words) > max_words else "")

# === Tavily helper ===
tavily_client = TavilyClient(api_key=TAVILY_API_KEY)

def fetch_ai_ml_news():
    """ Fetch AI/ML news and rewrite in Rancho’s style via LLM. """
    try:
        response = tavily_client.search(
            query="Artificial Intelligence Machine Learning",
            topic="news",
            days=3,
            max_results=3
        )
        results = response.get("results", [])
        if not results:
            return "Mere dost, abhi koi AI/ML news nahi mili. Thoda der baad try karo."

        # ✅ Clean titles, remove URLs
        news_list = [re.sub(r"http\S+", "", item["title"]).strip() for item in results]
        combined_news = " ".join(news_list)

        # ✅ Ask LLM to rewrite nicely in Rancho’s persona
        history_for_llm = [
            types.Content(role="user", parts=[types.Part(text=PERSONA)]),
            types.Content(role="user", parts=[types.Part(
                text=f"Summarize these AI/ML news headlines in less than 100 words in Rancho’s witty Hinglish style:\n{combined_news}"
            )])
        ]

        summary = []
        for chunk in llm_service.stream(history_for_llm):
            if chunk:
                summary.append(chunk)

        return enforce_word_limit("".join(summary).strip(), 100)

    except Exception as e:
        print("❌ Tavily error:", e)
        return "Sorry yaar, AI/ML news fetch karne mein gadbad ho gayi."

# === Main Class ===
class AssemblyAIStreamingTranscriber:
    def __init__(self, websocket: WebSocket, loop, sample_rate=16000):
        self.websocket = websocket
        self.loop = loop
        self.murf_ws = None
        self.chat_history: list[types.Content] = []

        self.client = StreamingClient(StreamingClientOptions(api_key=aai_api_key))
        self.client.on(StreamingEvents.Begin, self.on_begin)
        self.client.on(StreamingEvents.Turn, self.on_turn)
        self.client.on(StreamingEvents.Termination, self.on_termination)
        self.client.on(StreamingEvents.Error, self.on_error)
        self.client.connect(StreamingParameters(sample_rate=sample_rate, format_turns=False))

        # Persona injection
        self.chat_history.append(types.Content(role="user", parts=[types.Part(text=PERSONA)]))

    def on_begin(self, client, event: BeginEvent):
        print(f"🎤 Session started: {event.id}")

    def on_turn(self, client, event: TurnEvent):
        print(f"User transcript: {event.transcript} (end_of_turn={event.end_of_turn})")
        if event.end_of_turn and event.transcript.strip():
            asyncio.run_coroutine_threadsafe(
                self.websocket.send_json({"type": "transcript", "text": event.transcript}),
                self.loop
            )
            asyncio.run_coroutine_threadsafe(
                self.stream_llm_to_murf(event.transcript),
                self.loop
            )
            if not event.turn_is_formatted:
                client.set_params(StreamingSessionParameters(format_turns=True))

    async def stream_llm_to_murf(self, user_text: str):
        try:
            if not MURF_API_KEY:
                print("❌ Murf API key missing!")
                return

            murf_url = f"{MURF_WS_URL}?api-key={MURF_API_KEY}&sample_rate=44100&channel_type=MONO&format=WAV"
            if not self.murf_ws or not getattr(self.murf_ws, "open", False):
                self.murf_ws = await websockets.connect(murf_url)
                voice_config_msg = {
                    "voice_config": {
                        "voiceId": "hi-IN-amit",
                        "style": "Conversational",
                        "rate": 0,
                        "pitch": 0,
                        "variation": 1
                    }
                }
                await self.murf_ws.send(json.dumps(voice_config_msg))

            turn_chunk_id = 1

            async def receive_audio():
                nonlocal turn_chunk_id
                while True:
                    msg = await self.murf_ws.recv()
                    data = json.loads(msg)
                    if "audio" in data:
                        audio_b64 = data["audio"]
                        await self.websocket.send_json({
                            "type": "ai_audio",
                            "chunk_id": turn_chunk_id,
                            "audio": audio_b64
                        })
                        turn_chunk_id += 1
                    if data.get("final"):
                        await self.websocket.send_json({"type": "ai_audio", "final": True})
                        break

            async def send_llm_to_murf_and_client():
                try:
                    # ✅ Handle AI/ML news queries
                    if "ai news" in user_text.lower() or "ml news" in user_text.lower():
                        final_text = fetch_ai_ml_news()
                    else:
                        full_text = []
                        history_for_llm = self.chat_history + [
                            types.Content(role="user", parts=[types.Part(text=user_text)])
                        ]
                        for chunk in llm_service.stream(history_for_llm):
                            if not chunk:
                                continue
                            full_text.append(chunk)
                            await self.websocket.send_json({"type": "llm_text", "text": chunk})
                        final_text = enforce_word_limit("".join(full_text).strip(), 100)

                    await self.websocket.send_json({"type": "llm_text_final", "text": final_text})

                    # ✅ Send to Murf in chunks
                    for tts_chunk in split_into_chunks(clean_text_for_tts(final_text)):
                        await self.murf_ws.send(json.dumps({"text": tts_chunk, "end": False}))
                    await self.murf_ws.send(json.dumps({"text": "", "end": True}))

                    # ✅ Update chat history
                    self.chat_history.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
                    self.chat_history.append(types.Content(role="model", parts=[types.Part(text=final_text)]))

                except Exception as e:
                    print("❌ Error in send_llm_to_murf_and_client:", e)
                    await self.websocket.send_json({"type": "llm_text_final", "text": "[Error generating response]"})

            await asyncio.gather(receive_audio(), send_llm_to_murf_and_client())

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

    def close(self):
        self.client.disconnect(terminate=True)
