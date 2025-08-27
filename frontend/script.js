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

// Note: The relatedLinksDiv is now located inside the chat-llm-container in HTML
// so this variable is no longer necessary.

let stream, ws = null;
let micCtx, micSource, micProcessor;

let currentAIBubble = null;
let aiAccumulatedText = "";
let awaitingLinks = false;

let waveRAF = null;
let waveActive = false;

const SAMPLE_RATE = 44100;
const WS_URL = "ws://127.0.0.1:8000/ws";

const STATE_GIFS = {
  idle: "/static/images/robot.gif",
  listening: "/static/images/listening.gif",
  thinking: "/static/images/thinking.gif",
  speaking: "/static/images/speaking.gif",
};

// === New Audio Decode Helpers ===
let audioContext;
let playheadTime = 0;
let expectedChunk = 1;
let chunkBuffer = {};
let wavHeaderSet = true;

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
    updateState("idle");
  }
}

// === WebSocket + microphone ===
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


function updateState(newState) {
  console.log("🔄 State changed:", newState);
  if (STATE_GIFS[newState]) stateGif.src = STATE_GIFS[newState];
  statusDiv.innerText = newState === "idle" ?
    "Idle" :
    newState.charAt(0).toUpperCase() + newState.slice(1) + "...";

  gifWrap.style.display = (newState === "idle") ? "none" : "block";
  if (newState === "listening") stopWave();
}

// === Waveform ===
function startWave() {
  waveActive = true;
  const WIDTH = waveformCanvas.width;
  const HEIGHT = waveformCanvas.height;
  const draw = () => {
    if (!waveActive) return;
    ctx.clearRect(0, 0, WIDTH, HEIGHT);
    ctx.beginPath();
    const mid = HEIGHT / 2;
    for (let x = 0; x < WIDTH; x++) {
      const y = mid + Math.sin(x / 10 + Date.now() / 100) * 20;
      ctx.lineTo(x, y);
    }
    ctx.stroke();
    waveRAF = requestAnimationFrame(draw);
  };
  draw();
}
function stopWave() {
  waveActive = false;
  if (waveRAF) cancelAnimationFrame(waveRAF);
  ctx.clearRect(0, 0, waveformCanvas.width, waveformCanvas.height);
}

// === Mic + WebSocket ===
async function startRecording() {
  console.log("🎤 Starting recording, connecting to WebSocket:", WS_URL);
  
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    await audioContext.resume();
  }

  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    console.log("✅ WebSocket connected");
    updateState("listening");
  };
  ws.onclose = () => {
    console.log("❌ WebSocket closed");
    updateState("idle");
  };
  ws.onerror = (err) => console.error("⚠️ WebSocket error", err);

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    console.log("📩 WS message received:", msg);

    if (msg.type === "transcript") {
      currentAIBubble = null;
      aiAccumulatedText = "";
      awaitingLinks = false;
      addChatMessage("user", msg.text);
      updateState("thinking");
      startWave();
    } else if (msg.type === "llm_text") {
      aiAccumulatedText += msg.text;
      addOrUpdateAIMessage(aiAccumulatedText, false);
    } else if (msg.type === "llm_text_final") {
      aiAccumulatedText = msg.text || aiAccumulatedText;
      addOrUpdateAIMessage(aiAccumulatedText, true);
      awaitingLinks = msg.links_pending;
    } else if (msg.type === "related_links") {
      console.log("🔗 Related links received:", msg.links);
      let linksHtml = "<b>📰 Related News:</b><br>";
      msg.links.forEach(link => {
        linksHtml += `<div class="news-links"><a href="${link.url}" target="_blank">${link.title}</a></div>`;
      });
      addChatMessage("ai", linksHtml, true);
      awaitingLinks = false;
      currentAIBubble = null;
    } else if (msg.type === "ai_audio") {
      handleAudioChunk(msg.chunk_id, msg.audio, msg.final);
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
  if (ws) {
    ws.close();
    ws = null;
  }
  if (micProcessor) {
    micProcessor.disconnect();
    micSource.disconnect();
    micCtx.close();
  }
  if (stream) {
    stream.getTracks().forEach(track => track.stop());
  }
  stopWave();
  updateState("idle");
}

// === Chat UI ===
function addChatMessage(sender, text, isHtml = false) {
  const msgDiv = document.createElement("div");
  msgDiv.classList.add("chat-message", sender === "user" ? "user" : "ai");

  const bubble = document.createElement("div");
  bubble.classList.add("bubble");

  if (isHtml) {
    bubble.innerHTML = text;
  } else {
    bubble.textContent = text;
  }

  msgDiv.appendChild(bubble);
  chatContainer.appendChild(msgDiv);
  chatContainer.scrollTop = chatContainer.scrollHeight;
}

function addOrUpdateAIMessage(text, isFinal) {
  if (!currentAIBubble) {
    const msgDiv = document.createElement("div");
    msgDiv.classList.add("chat-message", "ai");
    currentAIBubble = document.createElement("div");
    currentAIBubble.classList.add("bubble");
    msgDiv.appendChild(currentAIBubble);
    chatContainer.appendChild(msgDiv);
  }
  currentAIBubble.innerHTML = text;
  chatContainer.scrollTop = chatContainer.scrollHeight;

  if (isFinal && !awaitingLinks) currentAIBubble = null;
}

// === Buttons ===
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
});

// init
updateState("idle");
console.log("✅ Chat UI initialized");