document.addEventListener("DOMContentLoaded", () => {
    // === DOM Elements and State Variables remain the same ===
    const messagesContainer = document.getElementById("messages");
    const chatHistoryContainer = document.getElementById("chat-history");
    const waveformCanvas = document.getElementById("chat-llm-waveform");
    const stateGif = document.getElementById("state-gif");
    const gifWrap = document.getElementById("chat-llm-status-gif");
    const ctx = waveformCanvas.getContext("2d");

    const startBtn = document.getElementById("chat-llm-start");
    const stopBtn = document.getElementById("chat-llm-stop");
    const resetBtn = document.getElementById("chat-llm-reset");
    const statusDiv = document.getElementById("chat-llm-status");

    const aaiKeyInput = document.getElementById("aai-key");
    const murfKeyInput = document.getElementById("murf-key");
    const tavilyKeyInput = document.getElementById("tavily-key");
    const geminiKeyInput = document.getElementById("gemini-key");
    const saveConfigBtn = document.getElementById("save-config-btn");

    const appContainer = document.querySelector(".app-container");
    const toggleSidebarBtn = document.getElementById("toggle-sidebar-btn");
    const introMessageDiv = document.getElementById("chat-start");

    let currentChatId = null;
    let chatHistory = JSON.parse(localStorage.getItem("chatHistory")) || {};
    let currentTheme = localStorage.getItem("theme") || "light";
    let isTyping = false;
    let stopGeneration = false;
    let isFirstInteraction = true;

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

    let audioContext;
    let playheadTime = 0;
    let expectedChunk = 1;
    let chunkBuffer = {};
    let isSpeaking = false;

    let audioQueue = [];
    let isPlaying = false;

    // === Core Functions ===
    function base64ToPCMFloat32(base64) {
        let binary = atob(base64);
        const wavHeaderSize = 44;
        // Check if the received data is a full WAV file with header
        const isWavFile = binary.length >= wavHeaderSize && binary.startsWith("RIFF");
        const offset = isWavFile ? wavHeaderSize : 0;
        
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

    // New function to play the next audio chunk from the queue
    function playNextChunk() {
        if (isPlaying || audioQueue.length === 0) {
            // If the queue is empty AND we're finished speaking, switch to idle.
            if (!isSpeaking) {
                updateState("idle");
                stopWave();
                startMicrophone();
            }
            return;
        }

        isPlaying = true;
        
        const float32Array = audioQueue.shift();

        if (!audioContext) {
            audioContext = new (window.AudioContext || window.webkitAudioContext)();
        }
        
        const buffer = audioContext.createBuffer(1, float32Array.length, audioContext.sampleRate);
        buffer.copyToChannel(float32Array, 0);
        const source = audioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(audioContext.destination);

        const now = audioContext.currentTime;
        if (playheadTime < now) playheadTime = now + 0.05;
        source.start(playheadTime);
        playheadTime += buffer.duration;

        source.onended = () => {
            isPlaying = false;
            playNextChunk(); // play the next one in the queue
        };
    }

    function handleAudioChunk(chunkId, base64Audio, isFinal) {
        if (base64Audio) {
            isSpeaking = true;
            if (expectedChunk === 1) {
                updateState("speaking");
                startWave();
            }

            // Add the chunk to the buffer
            chunkBuffer[chunkId] = base64Audio;

            // Process chunks from the buffer in sequence
            while (chunkBuffer[expectedChunk]) {
                const b64 = chunkBuffer[expectedChunk];
                delete chunkBuffer[expectedChunk];
                const float32Array = base64ToPCMFloat32(b64);
                if (float32Array) {
                    audioQueue.push(float32Array); // Enqueue the audio
                    if (!isPlaying) {
                        playNextChunk(); // Start playback if it's not already running
                    }
                }
                expectedChunk++;
            }
        }
        
        if (isFinal) {
            isSpeaking = false;
            // Once the final chunk is received and processed, reset and restart listening
            expectedChunk = 1;
            chunkBuffer = {};
            // The playNextChunk() function's onended callback will handle the state change back to idle
            // and the microphone restart after the final chunk plays.
        }
    }

    // === ALL OTHER FUNCTIONS ARE UNCHANGED ===
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
    
    function stopMicrophone() {
        if (micProcessor) {
            micProcessor.disconnect();
            micSource.disconnect();
            if (micCtx && micCtx.state !== 'closed') {
                micCtx.close();
            }
        }
        if (stream) {
            stream.getTracks().forEach(track => track.stop());
        }
    }

    async function startMicrophone() {
        try {
            if (stream) {
                console.log("Microphone is already running.");
                return;
            }
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
            updateState("listening");
        } catch (err) {
            console.error("Error accessing microphone:", err);
            updateState("idle");
            alert("Microphone access was denied. Please allow microphone permissions to use the voice chat feature.");
        }
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

    function startWave() {
        waveActive = true;
        const WIDTH = waveformCanvas.width;
        const HEIGHT = waveformCanvas.height;
        const draw = () => {
            if (!waveActive) return;
            ctx.clearRect(0, 0, WIDTH, HEIGHT);
            ctx.beginPath();
            const mid = HEIGHT / 2;
            ctx.strokeStyle = '#4A90E2';
            ctx.lineWidth = 2;
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

    function connectWebSocket() {
        console.log("Connecting WebSocket:", WS_URL);
        const aaiKey = localStorage.getItem("aaiKey");
        const murfKey = localStorage.getItem("murfKey");
        const tavilyKey = localStorage.getItem("tavilyKey");
        const geminiKey = localStorage.getItem("geminiKey");

        if (!aaiKey) {
            console.warn("AssemblyAI key not found. WebSocket will not send config.");
        }

        ws = new WebSocket(WS_URL);

        ws.onopen = () => {
            console.log("✅ WebSocket connected");
            ws.send(JSON.stringify({
                type: "config",
                aai_key: aaiKey,
                murf_key: murfKey,
                tavily_key: tavilyKey,
                gemini_key: geminiKey
            }));
            updateState("idle");
        };
        ws.onclose = () => {
            console.log("❌ WebSocket closed. Attempting to reconnect...");
            setTimeout(connectWebSocket, 3000);
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
                stopMicrophone(); // Stop mic when AI is about to respond
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
    }

    async function startRecording() {
        if (isFirstInteraction) {
            introMessageDiv.remove();
            isFirstInteraction = false;
        }
        if (!audioContext) {
            audioContext = new (window.AudioContext || window.webkitAudioContext)();
        }
        await audioContext.resume();
        await startMicrophone();
    }

    function stopRecording() {
        stopMicrophone();
        updateState("idle");
    }

    function addChatMessage(sender, text, isHtml = false) {
        const msgDiv = document.createElement("div");
        msgDiv.classList.add("message", sender === "user" ? "user" : "ai");
        const messageContent = document.createElement("div");
        messageContent.classList.add("message-content");
        if (isHtml) {
            messageContent.innerHTML = text;
        } else {
            messageContent.textContent = text;
        }
        msgDiv.appendChild(messageContent);
        messagesContainer.appendChild(msgDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    function addOrUpdateAIMessage(text, isFinal) {
        if (!currentAIBubble) {
            const msgDiv = document.createElement("div");
            msgDiv.classList.add("message", "ai");
            currentAIBubble = document.createElement("div");
            currentAIBubble.classList.add("message-content");
            msgDiv.appendChild(currentAIBubble);
            messagesContainer.appendChild(msgDiv);
        }
        currentAIBubble.innerHTML = text;
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
        if (isFinal && !awaitingLinks) currentAIBubble = null;
    }

    // === Button Event Listeners ===
    startBtn.addEventListener("click", async () => {
        startBtn.style.display = "none";
        stopBtn.style.display = "inline-block";
        await startRecording();
    });

    stopBtn.addEventListener("click", () => {
        stopRecording();
        startBtn.style.display = "inline-block";
        stopBtn.style.display = "none";
        updateState("idle");
    });

    resetBtn.addEventListener("click", () => {
        stopRecording();
        messagesContainer.innerHTML = "";
        updateState("idle");
        startBtn.style.display = "inline-block";
        stopBtn.style.display = "none";
        initIntroMessage();
        // Reset playback state
        audioQueue = [];
        isPlaying = false;
        playheadTime = 0;
        isSpeaking = false;
    });

    saveConfigBtn.addEventListener("click", () => {
        localStorage.setItem("aaiKey", aaiKeyInput.value);
        localStorage.setItem("murfKey", murfKeyInput.value);
        localStorage.setItem("tavilyKey", tavilyKeyInput.value);
        localStorage.setItem("geminiKey", geminiKeyInput.value);
        alert("API keys saved successfully!");
    });

    toggleSidebarBtn.addEventListener("click", () => {
        appContainer.classList.toggle("sidebar-closed");
        const icon = toggleSidebarBtn.querySelector("i");
        if (appContainer.classList.contains("sidebar-closed")) {
            icon.classList.remove("fa-bars");
            icon.classList.add("fa-arrow-right");
        } else {
            icon.classList.remove("fa-arrow-right");
            icon.classList.add("fa-bars");
        }
    });

    // === New Sidebar/History Functions ===
    function initIntroMessage() {
        const existingIntro = document.querySelector(".intro-message");
        if (!existingIntro) {
            messagesContainer.innerHTML = `
                <div id="chat-start">
                    <div class="intro-message">
                        <h1>Welcome to RanchoBytes AI</h1>
                        <p>Ask me anything. I'm specialized in AI news.</p>
                    </div>
                </div>
            `;
            isFirstInteraction = true;
        }
    }

    function updateChatHistorySidebar() {
        chatHistoryContainer.innerHTML = "";
        const historyKeys = Object.keys(chatHistory).sort((a, b) => chatHistory[b].timestamp - chatHistory[a].timestamp);
    }

    // Initial load
    init();

    function init() {
        aaiKeyInput.value = localStorage.getItem("aaiKey") || "";
        murfKeyInput.value = localStorage.getItem("murfKey") || "";
        tavilyKeyInput.value = localStorage.getItem("tavilyKey") || "";
        geminiKeyInput.value = localStorage.getItem("geminiKey") || "";
        updateState("idle");
        initIntroMessage();
        
        audioContext = new (window.AudioContext || window.webkitAudioContext)();

        connectWebSocket();
    }
});