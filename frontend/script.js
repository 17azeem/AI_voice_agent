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
let audioChunks = [];
let audioContext;
let analyser;
let source;
let animationId;
let stream;
let silenceTimeout;
let isRecording = false;
let listeningPaused = false;
let ws = null;
const WS_URL = "ws://127.0.0.1:8000/ws/audio"; 

const SILENCE_THRESHOLD = 0.02;
const SILENCE_DELAY = 1500;

let sessionId = "new";
let currentState = "listening";
let thinkingOffset = 0;

// State GIFs
const STATE_GIFS = {
  listening: "/static/images/listening.gif",
  thinking: "/static/images/thinking.gif",
  speaking: "/static/images/speaking.gif"
};

// Update state and GIF
function updateState(newState) {
  currentState = newState;
  if (STATE_GIFS[newState]) stateGif.src = STATE_GIFS[newState];
  statusDiv.innerText = newState.charAt(0).toUpperCase() + newState.slice(1) + "...";
}

// Draw waveform animation
function drawWaveform() {
  animationId = requestAnimationFrame(drawWaveform);
  ctx.clearRect(0, 0, waveformCanvas.width, waveformCanvas.height);

  if (currentState === "listening" && analyser) {
    const bufferLength = analyser.fftSize;
    const dataArray = new Uint8Array(bufferLength);
    analyser.getByteTimeDomainData(dataArray);

    ctx.lineWidth = 4;
    ctx.strokeStyle = "#4caf50";
    ctx.beginPath();

    let sliceWidth = waveformCanvas.width / bufferLength;
    let x = 0;
    let max = 0;

    for (let i = 0; i < bufferLength; i++) {
      let v = (dataArray[i] - 128) / 128.0;
      if (Math.abs(v) > max) max = Math.abs(v);
      let y = (v + 1) * waveformCanvas.height / 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
      x += sliceWidth;
    }
    ctx.stroke();

    // Auto-pause on silence
    if (max < SILENCE_THRESHOLD) {
      if (!silenceTimeout && isRecording) {
        silenceTimeout = setTimeout(() => {
          stopRecording(false); // stop recording but keep stream
          listeningPaused = true;
        }, SILENCE_DELAY);
      }
    } else {
      if (silenceTimeout) {
        clearTimeout(silenceTimeout);
        silenceTimeout = null;
      }
      // Resume recording if paused
      if (listeningPaused) {
        startRecording();
        listeningPaused = false;
      }
    }
  } else {
    // Thinking / speaking animation
    ctx.lineWidth = 4;
    ctx.strokeStyle = "#ff9800";
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
  }
}

// Initialize microphone
async function initStream() {
  if (!stream) {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioContext = new AudioContext();
    source = audioContext.createMediaStreamSource(stream);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 2048;
    source.connect(analyser);
  }
}

// 🔹 Helpers for WebSocket
function isWsOpen() {
  return ws && ws.readyState === WebSocket.OPEN;
}

async function ensureWebSocket() {
  if (isWsOpen()) return;

  // If already connecting, wait for it
  if (ws && ws.readyState === WebSocket.CONNECTING) {
    await new Promise((res) => ws.addEventListener("open", res, { once: true }));
    return;
  }

  ws = new WebSocket(WS_URL);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => console.log("✅ WS connected");
  ws.onclose = (e) => console.log("❌ WS closed", e.code, e.reason || "");
  ws.onerror = (err) => console.error("⚠️ WS error", err);
  ws.onmessage = (evt) => {
    // For this task, server likely just echoes/acks. Log it for visibility.
    try {
      console.log("WS msg:", evt.data);
    } catch (_) {}
  };

  await new Promise((res) => ws.addEventListener("open", res, { once: true }));
}

//before websocket
// // Start recording
// async function startRecording() {
//   await initStream();
//   if (isRecording) return;

//   mediaRecorder = new MediaRecorder(stream);
//   audioChunks = [];
//   isRecording = true;
//   updateState("listening");

//   mediaRecorder.ondataavailable = e => {
//     if (e.data.size > 0) audioChunks.push(e.data);
//   };

// mediaRecorder.onstop = async () => {
//   isRecording = false;
//   if (audioChunks.length === 0) return;

//   const audioBlob = new Blob(audioChunks, { type: "audio/webm" });
//   audioChunks = [];

//   // Set thinking state while waiting for AI response
//   updateState("thinking");

//   const formData = new FormData();
//   formData.append("audio", audioBlob, "recording.webm");

//   try {
//     const res = await fetch(`/agent/chat/${sessionId}`, { method: "POST", body: formData });
//     const data = await res.json();
//     if (data.session_id) sessionId = data.session_id;

//     addChatMessage("You", data.transcript);
//     addChatMessage("AI", data.llm_response);

//     // --- Sequential Audio Playback ---
//     if (data.audio_urls && data.audio_urls.length > 0) {
//       updateState("speaking");

//       let index = 0;

//       const playNext = () => {
//         if (index < data.audio_urls.length) {
//           const audio = new Audio(data.audio_urls[index]);
//           index++;
//           audio.play();
//           audio.onended = playNext;
//         } else {
//           updateState("listening");
//           startRecording();
//         }
//       };

//       playNext();
//     } else {
//       // Ensure thinking GIF is visible briefly
//       setTimeout(() => {
//         updateState("listening");
//         startRecording();
//       }, 200);
//     }

//   } catch (err) {
//     console.error(err);
//     setTimeout(() => {
//       updateState("listening");
//       startRecording();
//     }, 200);
//   }
// };


//   mediaRecorder.start();
//   drawWaveform();
// }

// // Stop recording manually
// async function stopRecording(resetStream = true) {
//   if (mediaRecorder && mediaRecorder.state !== "inactive") {
//     mediaRecorder.stop();
//     isRecording = false;
//   }

//   if (resetStream && stream) {
//     stream.getTracks().forEach(track => track.stop());
//     stream = null;
//     audioContext = null;
//     analyser = null;
//     source = null;
//   }
// }


// Start recording
async function startRecording() {
  await initStream();
  if (isRecording) return;

  // 🔹 Ensure WS is connected (kept open during silence pauses)
  await ensureWebSocket();

  // 🔹 Use MediaRecorder to capture & stream chunks over WS
  mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
  audioChunks = []; // not used in streaming mode, but keep cleared

  isRecording = true;
  updateState("listening");


  // --- NEW: stream chunks to server over WS ---
  mediaRecorder.ondataavailable = async (e) => {
    if (e.data.size > 0 && isWsOpen()) {
      const buf = await e.data.arrayBuffer();
      ws.send(buf); // send binary chunk
    }
  };


  // 🔹 Stream ~every 500ms
  
  mediaRecorder.start(500); // NEW: timeslice to emit chunks regularly

  drawWaveform();
}

// Stop recording manually
async function stopRecording(resetStream = true) {
  // Stop recorder if active
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
    isRecording = false;
  }

  // 🔹 When fully stopping (user pressed Stop), tell server we're done and close WS
  if (resetStream && isWsOpen()) {
    try { ws.send("END"); } catch (_) {}
    try { ws.close(); } catch (_) {}
    ws = null;
  }

  if (resetStream && stream) {
    stream.getTracks().forEach(track => track.stop());
    stream = null;
    audioContext = null;
    analyser = null;
    source = null;
  }
}


// Button events
startBtn.addEventListener("click", () => {
  document.getElementById("chat-llm-status-gif").style.display = "block";
  startBtn.style.display = "none";
  stopBtn.style.display = "inline-block";
  updateState("listening");
  startRecording();
});

stopBtn.addEventListener("click", () => {
  stopRecording();
  
});

resetBtn.addEventListener("click", async () => {
  document.getElementById("chat-llm-status-gif").style.display = "none";
  await stopRecording();
  chatContainer.innerHTML = "";
  ctx.clearRect(0, 0, waveformCanvas.width, waveformCanvas.height);
  sessionId = "new";
  thinkingOffset = 0;
  updateState("listening");
  startBtn.style.display = "inline-block";
  stopBtn.style.display = "none";
  resetBtn.style.display = "none";
});

// Add messages to chat
// Add messages to chat with proper styling
function addChatMessage(sender, text) {
  const msgDiv = document.createElement("div");
  msgDiv.classList.add("chat-message");
  msgDiv.classList.add(sender === "You" ? "user" : "ai"); // user = right, ai = left

  const bubble = document.createElement("div");
  bubble.classList.add("bubble");
  bubble.innerText = text;

  msgDiv.appendChild(bubble);
  chatContainer.appendChild(msgDiv);

  // Scroll to bottom automatically
  chatContainer.scrollTop = chatContainer.scrollHeight;
}


