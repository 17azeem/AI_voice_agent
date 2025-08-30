import os
import re
import asyncio
import json
import websockets
from fastapi import WebSocket
from datetime import datetime
from assemblyai.streaming.v3 import (
    StreamingClient, StreamingClientOptions,
    StreamingParameters, StreamingSessionParameters,
    StreamingEvents, BeginEvent, TurnEvent,
    TerminationEvent, StreamingError
)
from app.services.llm_service import LLMService
from tavily import TavilyClient

# --- Persona Prompt and Helpers ---
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
- Use casual, friendly English, but keep it clear and relatable. 
- Add small doses of humor, motivation, and “All is Well” attitude. 
- Encourage curiosity and practical learning instead of rote memorization. 
- Always give real-world analogies when explaining coding/AI/ML concepts. 
- Speak as if you’re a friend guiding the user, not a strict teacher. 
"""

def clean_text_for_tts(text: str) -> str:
    """Removes special characters to ensure clean text for text-to-speech."""
    return re.sub(r'[*_#`]', '', text).strip()

def split_into_chunks(text: str, max_len: int = 50):
    """Splits text into chunks of a maximum length, preserving sentences."""
    sentences = re.split(r'(?<=[.!?]) +', text)
    chunks, current_chunk = [], ""
    for sentence in sentences:
        if len(current_chunk) + len(sentence) < max_len:
            current_chunk += " " + sentence
        else:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            current_chunk = sentence
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    return chunks

def enforce_word_limit(text: str, max_words: int = 100) -> str:
    """Truncates text to a specified word limit."""
    words = text.split()
    return " ".join(words[:max_words]) + ("..." if len(words) > max_words else "")

def _extract_url_from_item(item: dict):
    """Tries a set of common keys to extract a usable URL."""
    for key in ("url", "link", "href", "canonical_url", "source_url"):
        val = item.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return None

def _clean_title(title: str):
    """Cleans a news title, removing URLs and enforcing a max length."""
    if not title:
        return "News"
    title = re.sub(r"http\S+", "", title).strip()
    if len(title) > 140:
        return title[:137] + "..."
    return title

async def fetch_ai_ml_news(llm_service: LLMService, tavily_client: TavilyClient):
    """Fetches and summarizes recent AI/ML news using Tavily and an LLM."""
    if not tavily_client:
        return "News service not configured, dost. Try later.", []

    try:
        today = datetime.now().strftime("%Y-%m-%d")
        query = f"Latest Artificial Intelligence and Machine Learning news {today}"
        response = tavily_client.search(
            query=query,
            topic="news",
            days=1,
            max_results=5,
            include_domains=["techcrunch.com", "theverge.com", "wired.com", "techspot.com", "manilatimes.net"]
        )
        results = (response or {}).get("results", []) or []
        if not results:
            return "Mere dost, abhi koi fresh AI/ML news nahi mili. Thoda der baad try karo.", []

        links = []
        seen_urls = set()
        for item in results:
            url = _extract_url_from_item(item)
            title = _clean_title(item.get("title") or "")
            if url and url not in seen_urls and title:
                seen_urls.add(url)
                links.append({"title": title, "url": url})
            if len(links) >= 3:
                break

        if not links:
            return "Koi news nahin mili yaar. Kuch aur pucho.", []
        
        titles = [l["title"] for l in links]
        combined_news = " ".join(titles)

        if not llm_service:
            print("LLM service is not configured.")
            return "Sorry yaar, LLM service is not ready. Can't summarize news.", []

        history_for_llm = [
            {"role": "user", "parts": [{"text": PERSONA}]},
            {"role": "user", "parts": [{"text": f"Summarize these AI/ML news headlines in less than 100 words in Rancho’s witty Hinglish style:\n{combined_news}"}]}
        ]
        
        summary_chunks = []
        async for chunk in llm_service.stream(history_for_llm):
            if chunk:
                summary_chunks.append(chunk)

        final_text = enforce_word_limit("".join(summary_chunks).strip(), 100)
        return final_text, links

    except Exception as e:
        print("❌ Tavily or LLM error during news fetch:", e)
        return "Sorry yaar, AI/ML news fetch karne mein gadbad ho gayi.", []

# --- Main Class ---
class AssemblyAIStreamingTranscriber:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.murf_ws = None
        self.chat_history: list[dict] = []
        self.murf_chunk_counter = 0
        self.aai_client = None
        self.llm_service = None
        self.tavily_client = None

    async def initialize_services(self, config_data: dict):
        """Initializes services with keys received from the frontend."""
        aai_key = config_data.get("aai_key")
        murf_key = config_data.get("murf_key")
        tavily_key = config_data.get("tavily_key")
        gemini_key = config_data.get("gemini_key")

        # Initialize LLM Service with Gemini key
        if gemini_key:
            self.llm_service = LLMService(api_key=gemini_key)
            self.chat_history.append({"role": "user", "parts": [{"text": PERSONA}]})
            print("✅ Gemini service configured.")
        else:
            print("❌ Gemini API key not provided.")

        # Initialize Tavily client with Tavily key
        if tavily_key:
            self.tavily_client = TavilyClient(api_key=tavily_key)
            print("✅ Tavily service configured.")
        else:
            print("❌ Tavily API key not provided.")

        # Initialize AssemblyAI Streaming Client
        if aai_key:
            try:
                self.aai_client = StreamingClient(StreamingClientOptions(api_key=aai_key))
                self.aai_client.on(StreamingEvents.Begin, self.on_begin_event)
                self.aai_client.on(StreamingEvents.Turn, self.on_turn_event)
                self.aai_client.on(StreamingEvents.Termination, self.on_termination_event)
                self.aai_client.on(StreamingEvents.Error, self.on_error_event)
                self.aai_client.connect(StreamingParameters(sample_rate=16000, format_turns=False))
                print("✅ AAI client initialized.")
            except Exception as e:
                print(f"❌ AAI client initialization error: {e}")
                self.aai_client = None
        else:
            print("❌ AssemblyAI API key not provided.")
        
        self.murf_api_key = murf_key

    def on_begin_event(self, client, event: BeginEvent):
        print(f"🎤 Session started: {event.id}")

    async def on_turn_event(self, client, event: TurnEvent):
        """Processes a complete turn of speech from the user."""
        if event.end_of_turn and event.transcript.strip():
            await self.websocket.send_json({"type": "transcript", "text": event.transcript})
            await self.stream_llm_to_murf(event.transcript)
            if not event.turn_is_formatted:
                client.set_params(StreamingSessionParameters(format_turns=True))

    async def _ensure_murf(self):
        """Ensures the Murf WebSocket connection is active."""
        if not self.murf_api_key:
            print("❌ Murf API key is not set.")
            return False
        
        try:
            if self.murf_ws and not self.murf_ws.closed:
                return True
            
            murf_url = f"wss://api.murf.ai/v1/speech/stream-input?api-key={self.murf_api_key}&sample_rate=44100&channel_type=MONO&format=WAV"
            self.murf_ws = await websockets.connect(murf_url)
            
            await self.murf_ws.send(json.dumps({
                "voice_config": {
                    "voiceId": "en-IN-eashwar",
                    "style": "Conversational",
                    "rate": 0,
                    "pitch": 0,
                    "variation": 1
                }
            }))
            return True
        except Exception as e:
            print("❌ Could not connect to Murf WS:", e)
            self.murf_ws = None
            return False

    async def stream_llm_to_murf(self, user_text: str):
        """Generates an LLM response and streams it to the user via TTS."""
        if not self.llm_service:
            await self.websocket.send_json({"type": "llm_text_final", "text": "Sorry, Gemini service is not configured.", "links_pending": False})
            return

        try:
            tts_ready = await self._ensure_murf()
            tts_task = None
            if tts_ready:
                tts_task = asyncio.create_task(self.receive_audio_from_murf())

            final_text = ""
            links = []

            is_news_query = any(k in user_text.lower() for k in ["ai news", "ml news", "tech news", "latest ai", "latest ml"])
            if is_news_query:
                final_text, links = await fetch_ai_ml_news(self.llm_service, self.tavily_client)
                await self.websocket.send_json({
                    "type": "llm_text_final",
                    "text": final_text,
                    "links_pending": bool(links)
                })
                if links:
                    await self.websocket.send_json({"type": "related_links", "links": links})
            else:
                full_text = []
                history_for_llm = self.chat_history + [
                    {"role": "user", "parts": [{"text": user_text}]}
                ]
                async for chunk in self.llm_service.stream(history_for_llm):
                    if chunk:
                        full_text.append(chunk)
                        await self.websocket.send_json({"type": "llm_text", "text": chunk})

                final_text = enforce_word_limit("".join(full_text).strip(), 100)
                await self.websocket.send_json({
                    "type": "llm_text_final",
                    "text": final_text,
                    "links_pending": False
                })

            if tts_ready and final_text:
                for tts_chunk in split_into_chunks(clean_text_for_tts(final_text)):
                    await self.murf_ws.send(json.dumps({"text": tts_chunk, "end": False}))
                await self.murf_ws.send(json.dumps({"text": "", "end": True}))

            if tts_task:
                await tts_task
            
            if final_text:
                self.chat_history.append({"role": "user", "parts": [{"text": user_text}]})
                self.chat_history.append({"role": "model", "parts": [{"text": final_text}]})

        except Exception as e:
            print("❌ General error in stream_llm_to_murf:", e)
            await self.websocket.send_json({"type": "llm_text_final", "text": "Maaf karna, kuch gadbad ho gayi.", "links_pending": False})

    async def receive_audio_from_murf(self):
        """Receives and relays audio chunks from Murf to the frontend."""
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(self.murf_ws.recv(), timeout=5.0)
                    if not msg:
                        break
                    
                    data = json.loads(msg)
                    if "audio" in data:
                        self.murf_chunk_counter += 1
                        await self.websocket.send_json({
                            "type": "ai_audio",
                            "chunk_id": self.murf_chunk_counter,
                            "audio": data["audio"],
                            "final": False
                        })
                except asyncio.TimeoutError:
                    print("Murf timeout, assuming stream is complete.")
                    break
                except websockets.exceptions.ConnectionClosed:
                    print("Murf connection closed, stream complete.")
                    break
        finally:
            await self.websocket.send_json({"type": "ai_audio", "final": True})
            self.murf_chunk_counter = 0

    def stream_audio(self, audio_chunk: bytes):
        """Streams microphone audio to the AssemblyAI client."""
        if self.aai_client:
            self.aai_client.stream(audio_chunk)

    def on_termination_event(self, client, event: TerminationEvent):
        print(f"🛑 Session terminated after {event.audio_duration_seconds}s")

    def on_error_event(self, client, error: StreamingError):
        print("❌ Streaming error:", error)

    async def close(self):
        """Properly closes all open connections."""
        if self.aai_client:
            self.aai_client.disconnect(terminate=True)
            self.aai_client = None
        if self.murf_ws and not self.murf_ws.closed:
            await self.murf_ws.close()
            self.murf_ws = None