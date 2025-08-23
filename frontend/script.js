// === Chat LLM Section ===
const startBtn = document.getElementById("chat-llm-start");
const stopBtn = document.getElementById("chat-llm-stop");
const resetBtn = document.getElementById("chat-llm-reset");
const statusDiv = document.getElementById("chat-llm-status");
const chatContainer = document.getElementById("chat-llm-container");
const waveformCanvas = document.getElementById("chat-llm-waveform");
const stateGif = document.getElementById("state-gif");
const ctx = waveformCanvas.getContext("2d");

let mediaRecorder;
let stream;
let ws = null; // WebSocket reference
let isRecording = false;
let animationId = null;
let thinkingOffset = 0;

// --- Audio playback variables ---
let audioContext;
let playheadTime;
let isPlaying = false;
let wavHeaderSet = true;

// 🔹 Ordered playback buffers
let expectedChunk = 1;
let chunkBuffer = {}; // store out-of-order chunks

const SAMPLE_RATE = 44100;
const WS_URL = "ws://127.0.0.1:8000/ws";

// GIF states
const STATE_GIFS = {
  listening: "/static/images/listening.gif",
  thinking: "/static/images/thinking.gif",
  speaking: "/static/images/speaking.gif"
};

// Update state and GIF
function updateState(newState) {
  if (STATE_GIFS[newState]) stateGif.src = STATE_GIFS[newState];
  statusDiv.innerText =
    newState.charAt(0).toUpperCase() + newState.slice(1) + "...";
}

// --- Audio Helpers ---
function base64ToPCMFloat32(base64) {
  let binary = atob(base64);
  const offset = wavHeaderSet ? 44 : 0; // Skip WAV header first time
  wavHeaderSet = false;
  const length = binary.length - offset;

  const buffer = new ArrayBuffer(length);
  const byteArray = new Uint8Array(buffer);
  for (let i = 0; i < byteArray.length; i++) {
    byteArray[i] = binary.charCodeAt(i + offset);
  }

  const view = new DataView(byteArray.buffer);
  const sampleCount = byteArray.length / 2;
  const float32Array = new Float32Array(sampleCount);

  for (let i = 0; i < sampleCount; i++) {
    const int16 = view.getInt16(i * 2, true);
    float32Array[i] = int16 / 32768;
  }
  return float32Array;
}

function playFloat32Array(float32Array) {
  const buffer = audioContext.createBuffer(1, float32Array.length, SAMPLE_RATE);
  buffer.copyToChannel(float32Array, 0);
  const source = audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(audioContext.destination);

  const now = audioContext.currentTime;
  if (playheadTime < now) playheadTime = now + 0.05; // small delay
  source.start(playheadTime);
  playheadTime += buffer.duration;
}

// --- Ordered playback ---
function handleAudioChunk(chunkId, base64Audio) {
  console.log(`🎧 Received chunk ${chunkId}, expected ${expectedChunk}`);

  // store chunk
  chunkBuffer[chunkId] = base64Audio;

  // try playing in order
  while (chunkBuffer[expectedChunk]) {
    const b64 = chunkBuffer[expectedChunk];
    delete chunkBuffer[expectedChunk];

    const float32Array = base64ToPCMFloat32(b64);
    if (float32Array) playFloat32Array(float32Array);

    expectedChunk++;
  }
}

// --- WebSocket + Recording ---
function floatTo16BitPCM(float32Array) {
  const buffer = new ArrayBuffer(float32Array.length * 2);
  const view = new DataView(buffer);
  let offset = 0;
  for (let i = 0; i < float32Array.length; i++, offset += 2) {
    let s = Math.max(-1, Math.min(1, float32Array[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

async function startRecording() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    console.log("🔗 WebSocket connected");
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    playheadTime = audioContext.currentTime;
    expectedChunk = 1;
    chunkBuffer = {};
  };
  ws.onclose = () => console.log("❌ WebSocket closed");
  ws.onerror = (err) => console.error("⚠️ WebSocket error", err);

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "transcript") {
      addChatMessage("User", msg.text);
    } else if (msg.type === "ai_response") {
      addChatMessage("AI", msg.text);
    } else if (msg.type === "ai_audio") {
      handleAudioChunk(msg.chunk_id, msg.audio);
    }
  };

  stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  audioCtx = new AudioContext({ sampleRate: 16000 });
  source = audioCtx.createMediaStreamSource(stream);
  processor = audioCtx.createScriptProcessor(4096, 1, 1);

  source.connect(processor);
  processor.connect(audioCtx.destination);

  processor.onaudioprocess = (e) => {
    const inputData = e.inputBuffer.getChannelData(0);
    const pcm16 = floatTo16BitPCM(inputData);
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(pcm16);
  };
}

function stopRecording() {
  if (processor) {
    processor.disconnect();
    processor.onaudioprocess = null;
  }
  if (source) source.disconnect();
  if (audioCtx) audioCtx.close();

  if (stream) stream.getTracks().forEach((t) => t.stop());
  if (ws) {
    ws.close();
    ws = null;
  }
}

// --- Chat UI ---
function addChatMessage(sender, text) {
  const msgDiv = document.createElement("div");
  msgDiv.classList.add("chat-message", sender === "User" ? "user" : "ai");

  const bubble = document.createElement("div");
  bubble.classList.add("bubble");
  bubble.innerText = text;

  msgDiv.appendChild(bubble);
  chatContainer.appendChild(msgDiv);
  chatContainer.scrollTop = chatContainer.scrollHeight;
}

// --- Buttons ---
startBtn.addEventListener("click", () => {
  startBtn.style.display = "none";
  stopBtn.style.display = "inline-block";
  updateState("listening");
  startRecording();
});

stopBtn.addEventListener("click", () => stopRecording());

resetBtn.addEventListener("click", () => {
  stopRecording();
  chatContainer.innerHTML = "";
  thinkingOffset = 0;
  updateState("listening");
  startBtn.style.display = "inline-block";
  stopBtn.style.display = "none";
  resetBtn.style.display = "none";
});
