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
let ws = null;
let isRecording = false;
let animationId = null;
let thinkingOffset = 0;

// WebSocket URL
const WS_URL = "ws://127.0.0.1:8000/ws/audio";

// GIF states
const STATE_GIFS = {
  listening: "/static/images/listening.gif",
  thinking: "/static/images/thinking.gif",
  speaking: "/static/images/speaking.gif"
};

// Update state and GIF
function updateState(newState) {
  if (STATE_GIFS[newState]) stateGif.src = STATE_GIFS[newState];
  statusDiv.innerText = newState.charAt(0).toUpperCase() + newState.slice(1) + "...";
}

// Draw waveform animation
function drawWaveform() {
  animationId = requestAnimationFrame(drawWaveform);
  ctx.clearRect(0, 0, waveformCanvas.width, waveformCanvas.height);

  if (isRecording) {
    // Simple sinus waveform while recording
    ctx.lineWidth = 4;
    ctx.strokeStyle = "#4caf50";
    ctx.beginPath();
    let sliceWidth = waveformCanvas.width / 100;
    let x = 0;
    for (let i = 0; i < 100; i++) {
      const y = waveformCanvas.height / 2 + Math.sin((i + thinkingOffset) * 0.3) * 30;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
      x += sliceWidth;
    }
    thinkingOffset += 3;
    ctx.stroke();
  } else {
    ctx.clearRect(0, 0, waveformCanvas.width, waveformCanvas.height);
  }
}

// Start recording
async function startRecording() {
  if (isRecording) return;

  // Open WebSocket
  ws = new WebSocket(WS_URL);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => console.log("✅ WS connected");
  ws.onclose = () => console.log("❌ WS closed");
  ws.onerror = (err) => console.error("⚠️ WS error", err);

  // Optional: handle transcription from server
  ws.onmessage = (evt) => {
    addChatMessage("AI", evt.data);
  };

  // Get microphone
  stream = await navigator.mediaDevices.getUserMedia({ audio: true });

  // MediaRecorder setup
  mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
  mediaRecorder.ondataavailable = async (e) => {
    if (e.data.size > 0 && ws.readyState === WebSocket.OPEN) {
      const buf = await e.data.arrayBuffer();
      ws.send(buf);
    }
  };

  mediaRecorder.onstop = () => {
    isRecording = false;
    console.log("🎙️ Recording stopped");
  };

  mediaRecorder.start(500); // send chunks every 500ms
  isRecording = true;
  updateState("listening");
  drawWaveform();
}

// Stop recording
function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
  if (stream) stream.getTracks().forEach(t => t.stop());
  if (ws && ws.readyState === WebSocket.OPEN) ws.send("END");
  if (ws) ws.close();
  isRecording = false;
  updateState("listening");
  ctx.clearRect(0, 0, waveformCanvas.width, waveformCanvas.height);
}

// Add messages to chat
function addChatMessage(sender, text) {
  const msgDiv = document.createElement("div");
  msgDiv.classList.add("chat-message", sender === "You" ? "user" : "ai");

  const bubble = document.createElement("div");
  bubble.classList.add("bubble");
  bubble.innerText = text;

  msgDiv.appendChild(bubble);
  chatContainer.appendChild(msgDiv);
  chatContainer.scrollTop = chatContainer.scrollHeight;
}

// Button events
startBtn.addEventListener("click", () => {
  document.getElementById("chat-llm-status-gif").style.display = "block";
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
