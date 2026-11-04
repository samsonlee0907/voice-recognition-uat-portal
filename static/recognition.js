(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const ids = ["speech", "realtime", "live"];
  const labels = { connecting: "正在連接", ready: "正在聆聽", failed: "這一欄已停止", ended: "錄音已結束", unavailable: "服務尚未設定" };
  let config, socket, peer, channel, stream, context, processor, source, timer, rtcTimer, startupTimer, clockTimer;
  let version = 0, phase = "idle", capture = false, stopPromise = null;
  let fragments = [], states = {}, rows = { speech: new Map(), realtime: new Map() };
  let processed = { speech: new Map(), realtime: new Map() };

  function report(type, detail) {
    if (document.body.hasAttribute("data-uat-workbench")) {
      window.dispatchEvent(new CustomEvent("recognition:" + type, { detail }));
    }
  }
  function controls() {
    document.body.dataset.comparePhase = phase;
    const busy = !["idle", "ended"].includes(phase);
    $("compare-start").disabled = busy || !config?.lanes.some(lane => lane.configured) || !$("compare-consent").checked;
    $("compare-stop").disabled = !busy || phase === "stopping";
    $("compare-clear").disabled = busy;
    $("compare-consent").disabled = busy;
    ids.forEach(id => { $(id + "-instructions").disabled = busy; $(id + "-instructions-reset").disabled = busy; });
    report("phase", { phase });
  }
  function send(event) {
    if (socket?.readyState !== WebSocket.OPEN) throw new Error("比較連線已中斷。");
    socket.send(JSON.stringify(event));
  }
  function resetText() {
    fragments = [];
    rows = { speech: new Map(), realtime: new Map() };
    processed = { speech: new Map(), realtime: new Map() };
    ids.forEach(id => { $(id + "-transcript").replaceChildren(); $(id + "-processed").replaceChildren(); $(id + "-notice").textContent = ""; });
  }
  function startCapture() {
    if (phase !== "connecting" || ids.some(id => states[id] === "connecting")) return;
    if (!ids.some(id => states[id] === "ready")) {
      $("compare-error").textContent = "沒有可用的辨識服務，請稍後重試。";
      stop().catch(fail);
      return;
    }
    phase = "listening";
    clearTimeout(startupTimer);
    startupTimer = null;
    capture = true;
    stream.getAudioTracks().forEach(track => { track.enabled = true; });
    processor.port.postMessage({ type: "microphone_gate", muted: false });
    const count = ids.filter(id => states[id] === "ready").length;
    $("compare-status").textContent = count === 3 ? "正在聆聽同一支麥克風。請自然說話，再停頓約兩秒查看完整轉錄。" :
      `正在聆聽。${count}/3 個服務可用；無法使用的欄位不會換成其他模型。`;
    timer = setTimeout(() => stop().catch(fail), config.session_seconds * 1000);
    const started = performance.now();
    clockTimer = setInterval(() => {
      const seconds = Math.max(0, config.session_seconds - Math.floor((performance.now() - started) / 1000));
      $("compare-clock").textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    }, 1000);
    controls();
  }
  function transcript(message) {
    const lane = rows[message.lane];
    if (!lane || !Number.isInteger(message.row) || message.row < 1 || message.row > 90 ||
        typeof message.text !== "string" || message.text.length > 4096 || typeof message.final !== "boolean") {
      throw new Error("轉錄事件格式不完整，已停止比較。");
    }
    let item = lane.get(message.row);
    if (item?.dataset.final === "true") return;
    if (!item) {
      item = document.createElement("li");
      item.value = message.row;
      lane.set(message.row, item);
    }
    item.textContent = message.text || (message.final ? "（未辨識到文字）" : "正在原生聽辨…");
    item.dataset.final = String(message.final);
    item.dataset.characters = String(message.text.length);
    while (lane.size > 60) lane.delete(Math.min(...lane.keys()));
    const target = $(message.lane + "-transcript");
    target.replaceChildren(...[...lane.entries()].sort((left, right) => left[0] - right[0]).map(entry => entry[1]));
    target.scrollTop = target.scrollHeight;
    report("result", { lane: message.lane, kind: "heard", row: message.row, text: message.text, final: message.final });
  }
  function native(message) {
    if (typeof message.delta !== "string" || message.delta.length > 512 ||
        !Number.isFinite(message.start_ms) || !Number.isFinite(message.end_ms) ||
        message.start_ms < 0 || message.end_ms < message.start_ms) throw new Error("原生片段的時間格式不符。");
    if (!Array.isArray(message.rows) || message.rows.length > 160 ||
        message.rows.some(row => typeof row.text !== "string" || typeof row.raw !== "string")) {
      throw new Error("原生片段的顯示格式不符。");
    }
    fragments = message.rows;
    renderNative();
    report("result", { lane: "live", kind: "heard-fragments", text: fragments.map(row => row.raw).join("\n") });
  }
  function renderNative() {
    $("live-transcript").replaceChildren(...fragments.map(row => {
      const item = document.createElement("li");
      item.textContent = row.raw;
      return item;
    }));
    $("live-transcript").scrollTop = $("live-transcript").scrollHeight;
  }
  function processedText(message) {
    const lane = processed[message.lane];
    if (!lane || !Number.isInteger(message.row) || message.row < 1 || message.row > 90 ||
        !["pending", "done", "failed", "cancelled", "skipped"].includes(message.status) ||
        (message.status === "done" && (typeof message.text !== "string" || message.text.length > 4096))) {
      throw new Error("模型後處理事件格式不符。");
    }
    let item = lane.get(message.row);
    if (!item) { item = document.createElement("li"); item.value = message.row; lane.set(message.row, item); }
    item.dataset.status = message.status;
    item.textContent = message.status === "done" ? message.text :
      message.status === "pending" ? `${config.postprocessing[message.lane]} 正在整理…` : message.message;
    if (["cancelled", "failed"].includes(message.status)) {
      const original = rows[message.lane]?.get(message.row);
      if (original?.dataset.final === "false" && original.dataset.characters === "0") original.textContent = "（原生聽辨未完成）";
    }
    while (lane.size > 60) lane.delete(Math.min(...lane.keys()));
    $(message.lane + "-processed").replaceChildren(...[...lane.entries()].sort((left, right) => left[0] - right[0]).map(entry => entry[1]));
    report("result", { lane: message.lane, kind: "processed", row: message.row, status: message.status,
      text: message.status === "done" ? message.text : String(message.message || "") });
  }
  function nativeProcessed(message) {
    if (!Array.isArray(message.rows) || message.rows.length > 160 ||
        message.rows.some(row => typeof row.raw !== "string" || row.raw.length > 160 * 512)) {
      throw new Error("原生模型後處理格式不符。");
    }
    $("live-processed").replaceChildren(...message.rows.map(row => {
      const item = document.createElement("li");
      item.textContent = row.raw;
      return item;
    }));
    report("result", { lane: "live", kind: "processed-fragments", text: message.rows.map(row => row.raw).join("\n") });
  }
  function closeNative() {
    if (rtcTimer) clearTimeout(rtcTimer);
    rtcTimer = null;
    if (peer) peer.onconnectionstatechange = null;
    channel?.close();
    peer?.close();
    channel = peer = null;
  }
  function nativeFailed() {
    closeNative();
    if (socket?.readyState === WebSocket.OPEN && phase !== "stopping") send({ type: "compare.live.failed" });
  }
  async function offer(current) {
    if (!config.lanes.find(lane => lane.id === "live")?.configured) return null;
    const connection = peer = new RTCPeerConnection();
    channel = connection.createDataChannel("oai-events");
    connection.addTransceiver(stream.getAudioTracks()[0], { direction: "sendrecv" });
    connection.onconnectionstatechange = () => {
      if (version !== current || peer !== connection || phase === "stopping") return;
      if (connection.connectionState === "connected") {
        if (rtcTimer) clearTimeout(rtcTimer);
        rtcTimer = null;
        if (states.live === "connecting") send({ type: "compare.live.connected" });
      } else if (["failed", "closed"].includes(connection.connectionState)) nativeFailed();
      else if (connection.connectionState === "disconnected" && !rtcTimer) rtcTimer = setTimeout(nativeFailed, 20000);
    };
    await connection.setLocalDescription(await connection.createOffer());
    return connection.localDescription.sdp;
  }
  async function releaseAudio(closePeer = true) {
    capture = false;
    if (timer) clearTimeout(timer);
    clearInterval(clockTimer);
    clockTimer = null;
    if (rtcTimer) clearTimeout(rtcTimer);
    timer = rtcTimer = null;
    clearTimeout(startupTimer);
    startupTimer = null;
    if (closePeer) closeNative();
    stream?.getTracks().forEach(track => track.stop());
    source?.disconnect();
    if (processor) { processor.port.onmessage = null; processor.disconnect(); }
    const previous = context;
    stream = source = processor = context = null;
    if (previous && previous.state !== "closed") await previous.close();
  }
  async function stop() {
    if (stopPromise) return stopPromise;
    stopPromise = (async () => {
      phase = "stopping";
      controls();
      $("compare-status").textContent = "正在結束收音並等待最後一段轉錄。";
      stream?.getAudioTracks().forEach(track => { track.enabled = false; });
      processor?.port.postMessage({ type: "microphone_gate", muted: true });
      if (capture && socket?.readyState === WebSocket.OPEN) await new Promise(resolve => setTimeout(resolve, 2000));
      capture = false;
      if (socket?.readyState === WebSocket.OPEN) {
        send({ type: "compare.stop" });
        await releaseAudio(false);
      } else {
        const previous = socket;
        socket = null;
        version++;
        previous?.close();
        await releaseAudio();
        finish();
      }
    })();
    try { await stopPromise; } finally { stopPromise = null; }
  }
  function finish() {
    phase = "ended";
    $("compare-status").textContent = "錄音已結束；文字保留在本頁供比對。";
    controls();
  }
  async function fail(cause) {
    $("compare-error").textContent = cause.message;
    version++;
    const previous = socket;
    socket = null;
    previous?.close();
    await releaseAudio();
    finish();
  }
  async function start() {
    $("compare-error").textContent = "";
    resetText();
    phase = "connecting";
    states = Object.fromEntries(config.lanes.map(lane => [lane.id, lane.configured ? "connecting" : "unavailable"]));
    ids.forEach(id => { $(id + "-status").textContent = labels[states[id]]; });
    controls();
    const current = ++version;
    startupTimer = setTimeout(() => {
      if (current === version && phase === "connecting") fail(new Error("麥克風或服務未在期限內準備完成，已停止比較。"));
    }, 60000);
    $("compare-status").textContent = "正在連接三個服務；準備好後才會開始送出語音。";
    try {
      const incoming = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }, video: false,
      });
      if (current !== version || phase !== "connecting") { incoming.getTracks().forEach(track => track.stop()); return; }
      stream = incoming;
      stream.getAudioTracks().forEach(track => { track.enabled = false; });
      stream.getAudioTracks()[0].addEventListener("ended", () => {
        if (stream === incoming && phase === "listening") fail(new Error("麥克風已中斷，請重新開始。"));
      });
      context = new AudioContext();
      await context.audioWorklet.addModule("/static/airline-service-worklet.js");
      if (current !== version || phase !== "connecting") return;
      processor = new AudioWorkletNode(context, "airline-service-processor", {
        numberOfInputs: 2, numberOfOutputs: 1, outputChannelCount: [1], channelCountMode: "explicit",
      });
      processor.port.postMessage({ type: "microphone_gate", muted: true });
      source = context.createMediaStreamSource(stream);
      source.connect(processor, 0, 0);
      processor.connect(context.destination);
      processor.port.onmessage = event => {
        if (!capture || current !== version) return;
        if (socket?.readyState !== WebSocket.OPEN || socket.bufferedAmount > 256 * 1024) {
          fail(new Error("音訊連線來不及傳送，已停止比較。")); return;
        }
        const stereo = new Uint8Array(event.data), mono = new Uint8Array(stereo.length / 2);
        for (let input = 0, output = 0; input < stereo.length; input += 4) {
          mono[output++] = stereo[input]; mono[output++] = stereo[input + 1];
        }
        let binary = "";
        for (const byte of mono) binary += String.fromCharCode(byte);
        send({ type: "compare.audio", audio: btoa(binary) });
      };
      await context.resume();
      const sdp = await offer(current);
      if (current !== version || phase !== "connecting") return;
      const ws = socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/recognition`);
      ws.onmessage = async ({ data }) => {
        if (socket !== ws || current !== version) return;
        try {
          const message = JSON.parse(data);
          if (message.type === "compare.ready") {
            if (phase === "connecting") send({
              type: "compare.start", consent: $("compare-consent").checked, sdp,
              instructions: Object.fromEntries(ids.map(id => [id, $(id + "-instructions").value])),
            });
          }
          else if (message.type === "compare.lane") {
            if (!ids.includes(message.lane) || !labels[message.status]) throw new Error("服務狀態格式不符。");
            states[message.lane] = message.status;
            document.querySelector(`[data-lane="${message.lane}"]`).dataset.status = message.status;
            if (message.lane === "live" && message.status === "failed") closeNative();
            $(message.lane + "-status").textContent = labels[message.status];
            if (message.message) $(message.lane + "-notice").textContent = message.message;
            report("lane", { lane: message.lane, status: message.status });
            startCapture();
            if (phase === "listening" && !ids.some(id => states[id] === "ready")) await stop();
          } else if (message.type === "compare.live.session") {
            if (phase !== "connecting" || states.live !== "connecting") return;
            if (!peer || typeof message.sdp !== "string" || message.sdp.length > 65536 || !message.sdp.startsWith("v=0")) throw new Error("原生語音設定不符。");
            const connection = peer;
            try {
              await connection.setRemoteDescription({ type: "answer", sdp: message.sdp });
            } catch (cause) {
              if (current !== version || phase !== "connecting" || peer !== connection) return;
              $("live-notice").textContent = "瀏覽器無法建立原生語音連線，其他欄位可繼續。";
              nativeFailed();
              return;
            }
            if (current !== version || phase !== "connecting" || peer !== connection) return;
            if (connection.connectionState !== "connected") rtcTimer = setTimeout(nativeFailed, 20000);
          } else if (message.type === "compare.transcript") transcript(message);
          else if (message.type === "compare.instructions") {
            ids.forEach(id => { $(id + "-instructions-status").textContent = `本次指令已固定 · ${message.fingerprints[id]}`; });
            report("instructions", { fingerprints: message.fingerprints });
          }
          else if (message.type === "compare.fragment") native(message);
          else if (message.type === "compare.processed") processedText(message);
          else if (message.type === "compare.native.processed") nativeProcessed(message);
          else if (message.type === "compare.notice") {
            if (!ids.includes(message.lane)) throw new Error("轉錄通知格式不符。");
            $(message.lane + "-notice").textContent = message.message;
          } else if (message.type === "compare.error") await fail(new Error(message.message));
          else if (message.type === "compare.ended") {
            if (config.lanes.find(lane => lane.id === "live")?.configured && !message.native_usage_final) {
              $("live-notice").textContent ||= "原生語音最終用量未獲服務確認；取消仍可能計費。";
            }
            const previous = socket;
            socket = null;
            previous?.close();
            version++;
            await releaseAudio();
            finish();
          }
        } catch (cause) { await fail(cause); }
      };
      ws.onerror = () => { $("compare-error").textContent = "比較服務無法連線，請重新登入或稍後重試。"; };
      ws.onclose = () => {
        if (socket === ws) fail(new Error("比較連線已中斷，已停止麥克風。"));
      };
    } catch (cause) { if (current === version && phase === "connecting") await fail(cause); }
  }
  $("compare-start").addEventListener("click", start);
  $("compare-stop").addEventListener("click", () => stop().catch(fail));
  $("compare-clear").addEventListener("click", resetText);
  $("compare-consent").addEventListener("change", controls);
  ids.forEach(id => $(id + "-instructions-reset").addEventListener("click", () => {
    $(id + "-instructions").value = config.instructions.defaults[id];
    $(id + "-instructions-status").textContent = "預設指令；下次錄音時套用。";
  }));
  window.addEventListener("pagehide", () => {
    version++;
    const previous = socket;
    socket = null;
    previous?.close();
    releaseAudio().catch(cause => { $("compare-error").textContent = cause.message; });
    resetText();
    finish();
  });
  fetch("/api/recognition/config").then(response => {
    if (!response.ok) throw new Error("無法讀取辨識服務設定，請登入後重試。");
    return response.json();
  }).then(document => {
    config = document;
    ids.forEach(id => {
      $(id + "-instructions").value = config.instructions.defaults[id];
      $(id + "-instructions").maxLength = config.instructions.max_length;
    });
    config.lanes.forEach(lane => { $(lane.id + "-status").textContent = lane.configured ? "尚未開始" : "服務尚未設定"; });
    $("compare-status").textContent = "同意測試語音用途後，即可開始。";
    controls();
    report("configured", { lanes: config.lanes, postprocessing: config.postprocessing });
  }).catch(cause => { $("compare-error").textContent = cause.message; });
})();
