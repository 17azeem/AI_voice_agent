// === Chat LLM Section ===
const startBtn = document.getElementById("chat-llm-start");
const stopBtn = document.getElementById("chat-llm-stop");
const resetBtn = document.getElementById("chat-llm-reset");
const statusDiv = document.getElementById("chat-llm-status");
const chatContainer = document.getElementById("chat-llm-container");
const waveformCanvas = document.getElementById("chat-llm-waveform");
const stateGif = document.getElementById("state-gif");
const gifWrap = document.getElementById("chat-llm-status-gif");
const ctx = waveformCanvas.getContext("2d");

let stream, ws = null;
let micCtx, micSource, micProcessor;
let audioContext, playheadTime;
let wavHeaderSet = true;

// Ordered playback
let expectedChunk = 1;
let chunkBuffer = {};

// AI streaming bubble
let currentAIBubble = null;
let aiAccumulatedText = "";

// Waveform animation
let waveRAF = null;
let waveActive = false;

const SAMPLE_RATE = 44100;
const WS_URL = "ws://127.0.0.1:8000/ws";

// GIF states
const STATE_GIFS = {
  idle: "/static/images/robot.gif",      
  listening: "/static/images/listening.gif",
  thinking: "/static/images/thinking.gif",
  speaking: "/static/images/speaking.gif",
};

function updateState(newState) {
  if (STATE_GIFS[newState]) {
    stateGif.src = STATE_GIFS[newState];
  }
  statusDiv.innerText = newState === "idle"
    ? "Idle"
    : newState.charAt(0).toUpperCase() + newState.slice(1) + "...";

  if (newState === "idle") {
    gifWrap.style.display = "none";
    stopWave();
  } else if (newState === "listening") {
    gifWrap.style.display = "block";
    stopWave();
  } else {
    gifWrap.style.display = "block";
  }
}

// Waveform animation
function startWave() {
  if (waveActive) return;
  waveActive = true;
  const w = waveformCanvas.width;
  const h = waveformCanvas.height;

  function draw(t) {
    if (!waveActive) return;
    ctx.clearRect(0, 0, w, h);
    ctx.lineWidth = 2;
    ctx.beginPath();
    const A = 20;
    const f = 0.004;
    const speed = 0.0025;
    for (let x = 0; x < w; x++) {
      const y = h / 2 + A * Math.sin((x * f) + t * speed);
      if (x === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
    waveRAF = requestAnimationFrame(draw);
  }
  waveRAF = requestAnimationFrame(draw);
}
function stopWave() {
  waveActive = false;
  if (waveRAF) cancelAnimationFrame(waveRAF);
  waveRAF = null;
  ctx.clearRect(0, 0, waveformCanvas.width, waveformCanvas.height);
}

// Audio decode helpers
function base64ToPCMFloat32(base64) {
  let binary = atob(base64);
  const offset = wavHeaderSet ? 44 : 0;
  wavHeaderSet = false;

  const length = binary.length - offset;
  const bytes = new Uint8Array(length);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = binary.charCodeAt(i + offset);
  }
  const view = new DataView(bytes.buffer);
  const sampleCount = bytes.length / 2;
  const float32Array = new Float32Array(sampleCount);
  for (let i = 0; i < sampleCount; i++) {
    const int16 = view.getInt16(i * 2, true);
    float32Array[i] = int16 / 32768;
  }
  return float32Array;
}
function playFloat32Array(float32Array) {
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    playheadTime = audioContext.currentTime;
  }
  const buffer = audioContext.createBuffer(1, float32Array.length, SAMPLE_RATE);
  buffer.copyToChannel(float32Array, 0);
  const source = audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(audioContext.destination);

  const now = audioContext.currentTime;
  if (playheadTime < now) playheadTime = now + 0.05;
  source.start(playheadTime);
  playheadTime += buffer.duration;
}

// Ordered playback per turn
function handleAudioChunk(chunkId, base64Audio, isFinal) {
  if (base64Audio) {
    updateState("speaking");
    startWave();

    chunkBuffer[chunkId] = base64Audio;

    while (chunkBuffer[expectedChunk]) {
      const b64 = chunkBuffer[expectedChunk];
      delete chunkBuffer[expectedChunk];
      const float32Array = base64ToPCMFloat32(b64);
      if (float32Array) playFloat32Array(float32Array);
      expectedChunk++;
    }
  }

  if (isFinal) {
    expectedChunk = 1;
    chunkBuffer = {};
    wavHeaderSet = true;
    updateState("listening");
  }
}

// WebSocket + microphone
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
    expectedChunk = 1;
    chunkBuffer = {};
    wavHeaderSet = true;
    updateState("listening");
  };
  ws.onclose = () => {
    console.log("❌ WebSocket closed");
    updateState("idle");
  };
  ws.onerror = (err) => console.error("⚠️ WebSocket error", err);

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    if (msg.type === "transcript") {
      addChatMessage("User", msg.text);
      updateState("thinking");
      startWave();
    }
    else if (msg.type === "llm_text") {
      aiAccumulatedText += msg.text;
      addOrUpdateAIMessage(aiAccumulatedText, false);
      updateState("thinking");
      startWave();
    }
    else if (msg.type === "llm_text_final") {
      aiAccumulatedText = msg.text;
      addOrUpdateAIMessage(aiAccumulatedText, true);
    }
    else if (msg.type === "ai_audio") {
      handleAudioChunk(msg.chunk_id, msg.audio, msg.final === true);
      if (msg.final === true) {
        aiAccumulatedText = "";
        currentAIBubble = null;
        stopWave();
      }
    }
  };

  stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  micCtx = new AudioContext({ sampleRate: 16000 });
  micSource = micCtx.createMediaStreamSource(stream);
  micProcessor = micCtx.createScriptProcessor(4096, 1, 1);
  micSource.connect(micProcessor);
  micProcessor.connect(micCtx.destination);

  micProcessor.onaudioprocess = (e) => {
    const inputData = e.inputBuffer.getChannelData(0);
    const pcm16 = floatTo16BitPCM(inputData);
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(pcm16);
  };
}

function stopRecording() {
  try {
    if (micProcessor) {
      micProcessor.disconnect();
      micProcessor.onaudioprocess = null;
      micProcessor = null;
    }
    if (micSource) {
      micSource.disconnect();
      micSource = null;
    }
    if (micCtx) {
      micCtx.close();
      micCtx = null;
    }
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      stream = null;
    }
    if (ws) {
      ws.close();
      ws = null;
    }
    stopWave();
    updateState("idle");
  } catch (e) {
    console.error(e);
  }
}

// Chat UI
function addChatMessage(sender, text) {
  const msgDiv = document.createElement("div");
  msgDiv.classList.add("chat-message", sender === "User" ? "user" : "ai");

  const bubble = document.createElement("div");
  bubble.classList.add("bubble");
  bubble.innerText = text;

  msgDiv.appendChild(bubble);
  chatContainer.appendChild(msgDiv);
  chatContainer.scrollTop = chatContainer.scrollHeight;

  if (sender === "AI") currentAIBubble = bubble;
}

function addOrUpdateAIMessage(text, isFinal) {
  if (!currentAIBubble) {
    addChatMessage("AI", "");
  }
  currentAIBubble.innerText = text;
  chatContainer.scrollTop = chatContainer.scrollHeight;
}

// Buttons
startBtn.addEventListener("click", () => {
  startBtn.style.display = "none";
  stopBtn.style.display = "inline-block";
  updateState("listening");
  startRecording();
});
stopBtn.addEventListener("click", () => {
  stopRecording();
  startBtn.style.display = "inline-block";
  stopBtn.style.display = "none";
  updateState("idle");
});
resetBtn.addEventListener("click", () => {
  stopRecording();
  chatContainer.innerHTML = "";
  updateState("idle");
  startBtn.style.display = "inline-block";
  stopBtn.style.display = "none";
  resetBtn.style.display = "none";
});

// initialize to idle on load
updateState("idle");
