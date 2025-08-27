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

# === Tavily helper ===
tavily_client = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None

def _extract_url_from_item(item: dict):
    """Try a set of common keys to extract a usable URL."""
    for key in ("url", "link", "href", "canonical_url", "source_url"):
        val = item.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    # sometimes in nested structures
    if isinstance(item.get("meta"), dict):
        for key in ("url", "link", "href"):
            v = item["meta"].get(key)
            if v:
                return v
    return None

def _clean_title(title: str):
    # strip any trailing urls from title, and limit length
    if not title:
        return "News"
    title = re.sub(r"http\S+", "", title).strip()
    if len(title) > 140:
        return title[:137] + "..."
    return title

def fetch_ai_ml_news():
    """Fetch AI/ML news and rewrite in Rancho’s style via LLM. Return (summary, [{title,url}, ...])."""
    if not tavily_client:
        return "News service not configured, dost. Try later.", []

    try:
        today = datetime.now().strftime("%Y-%m-%d")
        query = f"Latest Artificial Intelligence and Machine Learning news {today}"

        response = tavily_client.search(
            query=query,
            topic="news",
            days=1,
            max_results=5,  # fetch a few to increase chance of good results, trim later
            include_domains=["techcrunch.com", "theverge.com", "wired.com", "techspot.com", "manilatimes.net"]
        )
        results = (response or {}).get("results", []) or []
        if not results:
            return "Mere dost, abhi koi fresh AI/ML news nahi mili. Thoda der baad try karo.", []

        # extract usable links and clean titles, dedupe by url
        links = []
        seen_urls = set()
        for item in results:
            url = _extract_url_from_item(item)
            title = _clean_title(item.get("title") or item.get("headline") or item.get("short_title") or "")
            if not url:
                # try to parse any string fields for urls
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

        # If no links found, fallback: return titles as short list without links
        titles = [l["title"] for l in links] if links else [
            _clean_title(item.get("title", "")) for item in results[:3]
        ]
        combined_news = " ".join(titles)

        # Ask LLM to rewrite in Rancho persona
        history_for_llm = [
            types.Content(role="user", parts=[types.Part(text=PERSONA)]),
            types.Content(role="user", parts=[types.Part(
                text=f"Summarize these AI/ML news headlines in less than 100 words in Rancho’s witty Hinglish style:\n{combined_news}"
            )])
        ]
        summary_chunks = []
        try:
            for chunk in llm_service.stream(history_for_llm):
                if chunk:
                    summary_chunks.append(chunk)
        except Exception as e:
            # If streaming fails, fallback to joining titles
            print("❌ LLM streaming error while summarizing news:", e)
            summary_chunks = []

        final_text = enforce_word_limit("".join(summary_chunks).strip() or ("; ".join(titles)), 100)
        return final_text, links

    except Exception as e:
        print("❌ Tavily error:", e)
        return "Sorry yaar, AI/ML news fetch karne mein gadbad ho gayi.", []

# === Main Class ===
class AssemblyAIStreamingTranscriber:
    def __init__(self, websocket: WebSocket, loop, sample_rate=16000):
        self.websocket = websocket
        self.loop = loop
        self.murf_ws = None
        self.chat_history: list[types.Content] = []
        self.murf_chunk_counter = 0

        self.client = StreamingClient(StreamingClientOptions(api_key=aai_api_key))
        self.client.on(StreamingEvents.Begin, self.on_begin_event)
        self.client.on(StreamingEvents.Turn, self.on_turn_event)
        self.client.on(StreamingEvents.Termination, self.on_termination_event)
        self.client.on(StreamingEvents.Error, self.on_error_event)
        self.client.connect(StreamingParameters(sample_rate=sample_rate, format_turns=False))

        # persona seed
        self.chat_history.append(types.Content(role="user", parts=[types.Part(text=PERSONA)]))

    def on_begin_event(self, client, event: BeginEvent):
        print(f"🎤 Session started: {event.id}")

    def on_turn_event(self, client, event: TurnEvent):
        if event.end_of_turn and event.transcript.strip():
            # forward transcript to client
            asyncio.run_coroutine_threadsafe(
                self.websocket.send_json({"type": "transcript", "text": event.transcript}),
                self.loop
            )
            # process LLM & (optionally) TTS
            asyncio.run_coroutine_threadsafe(
                self.stream_llm_to_murf(event.transcript),
                self.loop
            )
            if not event.turn_is_formatted:
                client.set_params(StreamingSessionParameters(format_turns=True))

    async def _ensure_murf(self):
        """Connect to Murf WS if configured; return True if TTS ready."""
        if not MURF_API_KEY:
            return False
        try:
            if not self.murf_ws or not getattr(self.murf_ws, "open", False):
                murf_url = f"{MURF_WS_URL}?api-key={MURF_API_KEY}&sample_rate=44100&channel_type=MONO&format=WAV"
                self.murf_ws = await websockets.connect(murf_url)
                await self.murf_ws.send(json.dumps({
                    "voice_config": {
                        "voiceId": "hi-IN-amit",
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
                        print("✅ Sent related_links:", safe_links)
                    except Exception as e:
                        print("❌ Error sending related_links:", e)
            else:
                full_text = []
                history_for_llm = self.chat_history + [
                    types.Content(role="user", parts=[types.Part(text=user_text)])
                ]
                for chunk in llm_service.stream(history_for_llm):
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
                self.chat_history.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
                self.chat_history.append(types.Content(role="model", parts=[types.Part(text=final_text)]))

        except Exception as e:
            print("❌ Error in stream_llm_to_murf:", e)
            try:
                await self.websocket.send_json({"type": "llm_text_final", "text": "[Error generating response]", "links_pending": False})
            except Exception:
                pass

    async def receive_audio_from_murf(self):
        try:
            while True:
                msg = await self.murf_ws.recv()
                data = json.loads(msg)
                
                if "audio" in data:
                    self.murf_chunk_counter += 1
                    await self.websocket.send_json({
                        "type": "ai_audio",
                        "chunk_id": self.murf_chunk_counter, # Use an incrementing counter
                        "audio": data["audio"]
                    })
                
                if data.get("final"):
                    await self.websocket.send_json({"type": "ai_audio", "final": True})
                    self.murf_chunk_counter = 0 # Reset counter for next turn
                    break
        except Exception as e:
            print("❌ Murf receive error:", e)

    def stream_audio(self, audio_chunk: bytes):
        try:
            self.client.stream(audio_chunk)
        except Exception as e:
            print("❌ Error sending audio:", e)

    def on_termination_event(self, client, event: TerminationEvent):
        print(f"🛑 Session terminated after {event.audio_duration_seconds}s")

    def on_error_event(self, client, error: StreamingError):
        print("❌ Streaming error:", error)

    async def close_murf(self):
        if self.murf_ws:
            await self.murf_ws.close()
            self.murf_ws = None

    def close(self):
        self.client.disconnect(terminate=True)