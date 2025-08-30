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
# The 'types' import is no longer needed in this file
from tavily import TavilyClient

# Global services, initialized with None to be configured later
llm_service = None
tavily_client = None

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
- Use casual, friendly English, but keep it clear and relatable. 
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
            if current.strip():
                chunks.append(current.strip())
            current = s
    if current.strip():
        chunks.append(current.strip())
    return chunks

def enforce_word_limit(text: str, max_words: int = 100) -> str:
    words = text.split()
    return " ".join(words[:max_words]) + ("..." if len(words) > max_words else "")

def _extract_url_from_item(item: dict):
    """Try a set of common keys to extract a usable URL."""
    for key in ("url", "link", "href", "canonical_url", "source_url"):
        val = item.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    if isinstance(item.get("meta"), dict):
        for key in ("url", "link", "href"):
            v = item["meta"].get(key)
            if v:
                return v
    return None

def _clean_title(title: str):
    if not title:
        return "News"
    title = re.sub(r"http\S+", "", title).strip()
    if len(title) > 140:
        return title[:137] + "..."
    return title

def fetch_ai_ml_news():
    global tavily_client
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
            title = _clean_title(item.get("title") or item.get("headline") or item.get("short_title") or "")
            if not url:
                for v in item.values():
                    if isinstance(v, str):
                        m = re.search(r"https?://\S+", v)
                        if m:
                            url = m.group(0).rstrip(".,)")
                            break
            if url and url not in seen_urls:
                seen_urls.add(url)
                links.append({"title": title or "News", "url": url})
            if len(links) >= 3:
                break

        titles = [l["title"] for l in links] if links else [
            _clean_title(item.get("title", "")) for item in results[:3]
        ]
        combined_news = " ".join(titles)

        global llm_service
        if not llm_service:
            print("LLM service is not configured.")
            return "Sorry yaar, LLM service is not ready. Can't summarize news.", []

        history_for_llm = [
            {"role": "user", "parts": [{"text": PERSONA}]},
            {"role": "user", "parts": [{"text": f"Summarize these AI/ML news headlines in less than 100 words in Rancho’s witty Hinglish style:\n{combined_news}"}]}
        ]
        summary_chunks = []
        try:
            for chunk in llm_service.stream(history_for_llm):
                if chunk:
                    summary_chunks.append(chunk)
        except Exception as e:
            print("❌ LLM streaming error while summarizing news:", e)
            summary_chunks = []

        final_text = enforce_word_limit("".join(summary_chunks).strip() or ("; ".join(titles)), 100)
        return final_text, links

    except Exception as e:
        print("❌ Tavily error:", e)
        return "Sorry yaar, AI/ML news fetch karne mein gadbad ho gayi.", []

# === Main Class ===
class AssemblyAIStreamingTranscriber:
    def __init__(self, websocket: WebSocket, loop):
        self.websocket = websocket
        self.loop = loop
        self.murf_ws = None
        # Chat history is now a list of dictionaries
        self.chat_history: list[dict] = []
        self.murf_chunk_counter = 0
        self.client = None # AAI client will be set later
        self.llm_service = None
        self.tavily_client = None
        self.aai_api_key = None
        self.murf_api_key = None
        self.tavily_api_key = None
        self.gemini_api_key = None

    async def initialize_services(self, config_data: dict):
        """Initializes services with keys received from the frontend."""
        self.aai_api_key = config_data.get("aai_key")
        self.murf_api_key = config_data.get("murf_key")
        self.tavily_api_key = config_data.get("tavily_key")
        self.gemini_api_key = config_data.get("gemini_key")

        # Initialize LLM Service with Gemini key
        if self.gemini_api_key:
            global llm_service
            llm_service = LLMService(api_key=self.gemini_api_key)
            self.llm_service = llm_service
            # Persona seed using dictionary format
            self.chat_history.append({"role": "user", "parts": [{"text": PERSONA}]})
        else:
            print("❌ Gemini API key not provided.")

        # Initialize Tavily client with Tavily key
        if self.tavily_api_key:
            global tavily_client
            tavily_client = TavilyClient(api_key=self.tavily_api_key)
            self.tavily_client = tavily_client
        else:
            print("❌ Tavily API key not provided.")

        # Initialize AssemblyAI Streaming Client
        if self.aai_api_key:
            try:
                self.client = StreamingClient(StreamingClientOptions(api_key=self.aai_api_key))
                self.client.on(StreamingEvents.Begin, self.on_begin_event)
                self.client.on(StreamingEvents.Turn, self.on_turn_event)
                self.client.on(StreamingEvents.Termination, self.on_termination_event)
                self.client.on(StreamingEvents.Error, self.on_error_event)
                self.client.connect(StreamingParameters(sample_rate=16000, format_turns=False))
                print("✅ AAI client initialized.")
            except Exception as e:
                print(f"❌ AAI client initialization error: {e}")
                self.client = None
        else:
            print("❌ AssemblyAI API key not provided.")

    def on_begin_event(self, client, event: BeginEvent):
        print(f"🎤 Session started: {event.id}")

    def on_turn_event(self, client, event: TurnEvent):
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

    async def _ensure_murf(self):
        if not self.murf_api_key:
            print("❌ Murf API key is not set.")
            return False
        try:
            # Check if Murf WS is already connected and not closed
            if self.murf_ws and not self.murf_ws.closed:
                return True
            
            # If not, establish a new connection
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
            print("❌ Could not init Murf WS:", e)
            self.murf_ws = None
            return False

    async def stream_llm_to_murf(self, user_text: str):
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

            if any(k in user_text.lower() for k in ["ai news", "ml news", "tech news", "latest ai", "latest ml"]):
                final_text, links = fetch_ai_ml_news()
                await self.websocket.send_json({
                    "type": "llm_text_final",
                    "text": final_text,
                    "links_pending": bool(links)
                })
                if links:
                    safe_links = [{"title": l.get("title", "News"), "url": l.get("url", "#")} for l in links]
                    try:
                        await self.websocket.send_json({"type": "related_links", "links": safe_links})
                    except Exception as e:
                        print("❌ Error sending related_links:", e)
            else:
                full_text = []
                history_for_llm = self.chat_history + [
                    {"role": "user", "parts": [{"text": user_text}]}
                ]
                for chunk in self.llm_service.stream(history_for_llm):
                    if not chunk:
                        continue
                    full_text.append(chunk)
                    try:
                        await self.websocket.send_json({"type": "llm_text", "text": chunk})
                    except Exception:
                        pass
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
            print("❌ Error in stream_llm_to_murf:", e)
            try:
                await self.websocket.send_json({"type": "llm_text_final", "text": "[Error generating response]", "links_pending": False})
            except Exception:
                pass

    # REVISED: The finally block now checks if the WebSocket is not closed.
    async def receive_audio_from_murf(self):
        try:
            while True:
                # Check for messages from Murf
                try:
                    msg = await asyncio.wait_for(self.murf_ws.recv(), timeout=5.0)
                    if not msg:
                        # An empty message is a valid end-of-stream signal
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

            await self.websocket.send_json({"type": "ai_audio", "final": True})
            self.murf_chunk_counter = 0

        except Exception as e:
            print("❌ Murf receive error:", e)
        finally:
            if self.murf_ws and not self.murf_ws.closed:
                await self.murf_ws.close()
            self.murf_ws = None
            
    def stream_audio(self, audio_chunk: bytes):
        if self.client:
            self.client.stream(audio_chunk)

    def on_termination_event(self, client, event: TerminationEvent):
        print(f"🛑 Session terminated after {event.audio_duration_seconds}s")

    def on_error_event(self, client, error: StreamingError):
        print("❌ Streaming error:", error)

    async def close_murf(self):
        if self.murf_ws and not self.murf_ws.closed:
            await self.murf_ws.close()
            self.murf_ws = None

    def close(self):
        if self.client:
            self.client.disconnect(terminate=True)
            self.client = None