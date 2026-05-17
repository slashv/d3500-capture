const statusPollMs = 1000;

const els = {
  cameraLine: document.querySelector("#camera-line"),
  statePill: document.querySelector("#state-pill"),
  stateValue: document.querySelector("#state-value"),
  previewValue: document.querySelector("#preview-value"),
  formatValue: document.querySelector("#format-value"),
  errorValue: document.querySelector("#error-value"),
  commandValue: document.querySelector("#command-value"),
  latestCapture: document.querySelector("#latest-capture"),
  previewImage: document.querySelector("#preview-image"),
  previewPlaceholder: document.querySelector("#preview-placeholder"),
  detectButton: document.querySelector("#detect-button"),
  startButton: document.querySelector("#start-button"),
  stopButton: document.querySelector("#stop-button"),
  captureButton: document.querySelector("#capture-button"),
  recoverButton: document.querySelector("#recover-button"),
};

let previewVisible = false;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { Accept: "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      if (data.detail) message = data.detail;
    } catch {
      // Keep the HTTP status message.
    }
    throw new Error(message);
  }
  return response.json();
}

async function post(path) {
  return api(path, { method: "POST" });
}

function setBusy(isBusy) {
  els.detectButton.disabled = isBusy;
  els.startButton.disabled = isBusy;
  els.stopButton.disabled = isBusy;
  els.captureButton.disabled = isBusy;
  els.recoverButton.disabled = false;
}

function showPreview(running) {
  if (running && !previewVisible) {
    els.previewImage.src = `/live.mjpg?t=${Date.now()}`;
    els.previewImage.classList.add("visible");
    els.previewPlaceholder.classList.add("hidden");
    previewVisible = true;
  } else if (!running && previewVisible) {
    els.previewImage.removeAttribute("src");
    els.previewImage.classList.remove("visible");
    els.previewPlaceholder.classList.remove("hidden");
    previewVisible = false;
  }
}

function renderStatus(status) {
  const state = status.state || "unknown";
  const previewRunning = Boolean(status.preview?.running);
  const camera = status.camera || {};
  const settings = status.settings || {};

  els.statePill.textContent = state;
  els.statePill.dataset.state = state;
  els.stateValue.textContent = state;
  els.previewValue.textContent = previewRunning ? "running" : "stopped";
  els.formatValue.textContent = settings.capture_format || "unknown";
  els.errorValue.textContent = status.error || "none";
  els.commandValue.textContent = status.active_command
    ? status.active_command.join(" ")
    : "none";
  els.cameraLine.textContent = camera.detected
    ? `${camera.model} on ${camera.port}`
    : `${camera.model || "Camera"} not detected`;

  showPreview(previewRunning);
  renderLatest(status.latest_capture);
}

function renderLatest(capture) {
  if (!capture) {
    els.latestCapture.className = "latest-empty";
    els.latestCapture.textContent = "No capture yet";
    return;
  }

  const thumbnailUrl = capture.jpeg_path ? capture.file_url : "";
  const imageUrl = `${thumbnailUrl}?t=${Date.now()}`;
  const paths = [
    capture.file_path && `file: ${capture.file_path}`,
    capture.raw_path && `raw: ${capture.raw_path}`,
    capture.jpeg_path && `jpeg: ${capture.jpeg_path}`,
  ].filter(Boolean);

  els.latestCapture.className = "latest-capture";
  els.latestCapture.innerHTML = `
    <a href="${capture.file_url}" target="_blank" rel="noreferrer">
      ${
        thumbnailUrl
          ? `<img src="${imageUrl}" alt="Latest capture" />`
          : `<div class="file-tile">FILE</div>`
      }
    </a>
    <div>
      <strong>${capture.id}</strong>
      <span>${capture.created_at || ""}</span>
      <code>${paths.join("\n")}</code>
    </div>
  `;
}

async function refreshStatus() {
  try {
    const status = await api("/status");
    renderStatus(status);
  } catch (error) {
    els.statePill.textContent = "offline";
    els.statePill.dataset.state = "error";
    els.errorValue.textContent = error.message;
  }
}

async function runAction(action) {
  setBusy(true);
  try {
    const result = await action();
    if (result && result.state) renderStatus(result);
    await refreshStatus();
  } catch (error) {
    els.errorValue.textContent = error.message;
  } finally {
    setBusy(false);
  }
}

els.detectButton.addEventListener("click", () => runAction(() => post("/detect")));
els.startButton.addEventListener("click", () => runAction(() => post("/preview/start")));
els.stopButton.addEventListener("click", () => runAction(() => post("/preview/stop")));
els.captureButton.addEventListener("click", () => runAction(() => post("/capture")));
els.recoverButton.addEventListener("click", () => runAction(() => post("/recover")));

refreshStatus();
setInterval(refreshStatus, statusPollMs);
