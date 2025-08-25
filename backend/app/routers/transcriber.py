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

llm_service = LLMService()
aai_api_key = os.getenv("ASSEMBLYAI_API_KEY")
MURF_WS_URL = os.getenv("MURF_TTS_WS", "wss://api.murf.ai/v1/speech/stream-input")
MURF_API_KEY = os.getenv("MURF_API_KEY")

# === Persona Prompt (as first user message, not system) ===
PERSONA = """ 
You are an AI agent taking the persona of Rancho (from the movie 3 Idiots). 
Your role is to answer questions with wit, humor, and simple explanations.
Answer the each question in less than 50 words.
You are deeply knowledgeable in Artificial Intelligence, Machine Learning, Python, Java, and coding concepts. 
You break down complex technical topics into easy, practical examples, just like Rancho would explain things in class. 

Conversational Style Guidelines:
- Use casual, friendly Hinglish (mix of Hindi + English), but keep it clear and relatable. 
- Add small doses of humor, motivation, and “All is Well” attitude. 
- Encourage curiosity and practical learning instead of rote memorization. 
- Always give real-world analogies when explaining coding/AI/ML concepts. 
- Speak as if you’re a friend guiding the user, not a strict teacher. 

Examples of Dialogues & Situations:

User: Rancho, I feel stressed about exams.  
Rancho (AI): Arre yaar, stress ka toh kaam hi hai pressure banana. Tu bas samajhne pe focus kar—marks toh apne aap peeche aa jayenge. Samajh gaya?  

User: I don’t know what career to choose.  
Rancho (AI): Simple hai, dost. Dil se puchh—kya cheez karte hue tu time ka track bhool jaata hai? Wahi tera career hai. Passion ke peeche bhaag, success toh apne aap miljayegi.  

User: Everyone is ahead of me, I feel left behind.  
Rancho (AI): Race ghode jeet-te hain, insaan nahi. Tu bas apna kaam karo, comparison ka load chhod de. Life mein “All is Well” mantra yaad rakh.  

User: Rancho, what is Machine Learning?  
Rancho (AI): ML matlab ek dost ko baar-baar samjhana ki yeh cheez aise hoti hai… aur ek din woh khud karne lage bina tujhe bulaye. Computer ko bhi data se “experience” dilwate ho, aur woh khud seekh leta hai. Samajh aaya?  

User: Rancho, I am not able to understand recursion in coding.  
Rancho (AI): Arre recursion matlab apni mummy ko phone karna aur kehna “maa, khana bana do”, aur mummy bole “pehle tu chawal dhoke rakh de”. Matlab ek problem ko solve karne ke liye chhoti problem pehle solve karni. Bas ek din khud coding karke dekh, recursion dost ban jayega.  

User: Rancho, explain Python lists vs tuples.  
Rancho (AI): Lists are like your messy hostel room — you can move things around anytime. Tuples are like marriage — once committed, you can’t change. Use lists for flexibility, tuples for stability.

Always stay in Rancho persona, maintain humor, and make even the toughest AI/ML/coding concepts sound simple, practical, and motivating.
"""

# === Helpers: clean + chunk text for TTS ===
def clean_text_for_tts(text: str) -> str:
    """Remove markdown-like characters before TTS."""
    return re.sub(r'[*_#`]', '', text).strip()

def split_into_chunks(text: str, max_len: int = 50):
    """Split into smaller chunks by sentence boundaries for TTS."""
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


class AssemblyAIStreamingTranscriber:
    def __init__(self, websocket: WebSocket, loop, sample_rate=16000):
        self.websocket = websocket
        self.loop = loop
        self.murf_ws = None  # Murf websocket connection
        self.chat_history: list[types.Content] = []

        # Initialize AssemblyAI client
        self.client = StreamingClient(StreamingClientOptions(api_key=aai_api_key))
        self.client.on(StreamingEvents.Begin, self.on_begin)
        self.client.on(StreamingEvents.Turn, self.on_turn)
        self.client.on(StreamingEvents.Termination, self.on_termination)
        self.client.on(StreamingEvents.Error, self.on_error)

        self.client.connect(
            StreamingParameters(sample_rate=sample_rate, format_turns=False)
        )

        # Persona injection (as "user" message)
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
            print(f"🌐 Murf WS URL: {murf_url}")

            if not self.murf_ws or not getattr(self.murf_ws, "open", False):
                self.murf_ws = await websockets.connect(murf_url)
                print("🎤 Murf WS connected")
                voice_config_msg = {
                    "voice_config": {
                        "voiceId": "hi-IN-kabir",
                        "style": "Conversational",
                        "rate": 0,
                        "pitch": 0,
                        "variation": 1
                    }
                }
                await self.murf_ws.send(json.dumps(voice_config_msg))
                print("✅ Voice config sent to Murf")

            turn_chunk_id = 1

            async def receive_audio():
                nonlocal turn_chunk_id
                try:
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
                            print(f"🔊 Sent Murf audio chunk #{turn_chunk_id}")
                            turn_chunk_id += 1

                        if data.get("final"):
                            await self.websocket.send_json({"type": "ai_audio", "final": True})
                            print("✅ Murf TTS completed for this turn")
                            break
                except Exception as e:
                    print("❌ Error receiving Murf audio:", e)

            async def send_llm_to_murf_and_client():
                full_text = []
                try:
                    history_for_llm = self.chat_history + [
                        types.Content(role="user", parts=[types.Part(text=user_text)])
                    ]

                    for chunk in llm_service.stream(history_for_llm):
                        if not chunk:
                            continue
                        full_text.append(chunk)
                        await self.websocket.send_json({"type": "llm_text", "text": chunk})

                    final_text = "".join(full_text).strip()
                    await self.websocket.send_json({"type": "llm_text_final", "text": final_text})

                    # ✅ Clean + chunk text for Murf
                    for tts_chunk in split_into_chunks(clean_text_for_tts(final_text)):
                        await self.murf_ws.send(json.dumps({"text": tts_chunk, "end": False}))
                    await self.murf_ws.send(json.dumps({"text": "", "end": True}))

                    # ✅ Update conversation history
                    self.chat_history.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
                    self.chat_history.append(types.Content(role="model", parts=[types.Part(text=final_text)]))

                except Exception as e:
                    print("❌ Error streaming LLM/Murf:", e)
                    try:
                        await self.websocket.send_json({"type": "llm_text_final", "text": "[Error generating response]"})
                    except Exception:
                        pass

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
