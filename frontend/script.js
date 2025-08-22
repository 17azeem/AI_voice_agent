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

// WebSocket URL
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

// Draw waveform animation
function drawWaveform() {
  animationId = requestAnimationFrame(drawWaveform);
  ctx.clearRect(0, 0, waveformCanvas.width, waveformCanvas.height);

  if (isRecording) {
    ctx.lineWidth = 4;
    ctx.strokeStyle = "#4caf50";
    ctx.beginPath();
    let sliceWidth = waveformCanvas.width / 100;
    let x = 0;
    for (let i = 0; i < 100; i++) {
      const y =
        waveformCanvas.height / 2 +
        Math.sin((i + thinkingOffset) * 0.3) * 30;
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

/*  Start Recording with Web Audio API */
async function startRecording() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => console.log("🔗 WebSocket connected");
  ws.onclose = () => console.log("❌ WebSocket closed");
  ws.onerror = (err) => console.error("⚠️ WebSocket error", err);

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    if (msg.type === "transcript") {
      addChatMessage("User", msg.text);
    } else if (msg.type === "ai_response") {
      addChatMessage("AI", msg.text); // streamed LLM response
    } else if (msg.type === "ai_audio") {
      console.log(
        "✅ Client received audio chunk (base64 length=" +
          msg.audio.length +
          ")"
      );
      // Just acknowledge receipt of base64 audio
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
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(pcm16);
    }
  };
}

/*  Stop Recording */
function stopRecording() {
  if (processor) {
    processor.disconnect();
    processor.onaudioprocess = null;
  }
  if (source) source.disconnect();
  if (audioCtx) audioCtx.close();

  if (stream) {
    stream.getTracks().forEach((track) => track.stop());
  }
  if (ws) {
    ws.close();
    ws = null; // reset to null for reconnect logic
  }
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
