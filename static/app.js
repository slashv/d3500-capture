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
  exposureControls: document.querySelector("#exposure-controls"),
  focusControls: document.querySelector("#focus-controls"),
  imageControls: document.querySelector("#image-controls"),
  detectButton: document.querySelector("#detect-button"),
  startButton: document.querySelector("#start-button"),
  stopButton: document.querySelector("#stop-button"),
  captureButton: document.querySelector("#capture-button"),
  recoverButton: document.querySelector("#recover-button"),
  refreshConfigButton: document.querySelector("#refresh-config-button"),
  autofocusButton: document.querySelector("#autofocus-button"),
};

const configContainers = {
  exposure: els.exposureControls,
  focus: els.focusControls,
  image: els.imageControls,
};

const configOrder = {
  exposure: ["aperture", "shutter_speed", "iso", "exposure_compensation", "metering"],
  focus: ["focus_mode", "live_view_af_mode", "live_view_af_focus"],
  image: ["white_balance", "image_size", "image_quality", "capture_mode"],
};

let previewVisible = false;
let cameraControls = {};
let renderedCaptureId = null;

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

async function post(path, body) {
  return api(path, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function put(path, body) {
  return api(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function setBusy(isBusy) {
  document.querySelectorAll("button, select").forEach((element) => {
    element.disabled = isBusy;
  });
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
    renderedCaptureId = null;
    els.latestCapture.className = "latest-empty";
    els.latestCapture.textContent = "No capture yet";
    return;
  }
  if (renderedCaptureId === capture.id) return;
  renderedCaptureId = capture.id;

  const thumbnailUrl = capture.jpeg_path ? capture.file_url : "";
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
          ? `<img src="${thumbnailUrl}" alt="Latest capture" />`
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

function renderCameraConfig(controls) {
  cameraControls = controls || {};

  for (const [group, container] of Object.entries(configContainers)) {
    const keys = configOrder[group] || [];
    container.innerHTML = keys
      .map((key) => renderControl(cameraControls[key]))
      .filter(Boolean)
      .join("");
  }

  document.querySelectorAll("[data-config-key]").forEach((select) => {
    select.addEventListener("change", () => {
      const key = select.dataset.configKey;
      runAction(async () => {
        const result = await put(`/camera/config/${key}`, { value: select.value });
        cameraControls[key] = result.control;
        renderCameraConfig(cameraControls);
        if (result.status) renderStatus(result.status);
        return result.status || {};
      });
    });
  });
}

function renderControl(control) {
  if (!control) return "";
  if (control.type !== "RADIO" || !control.choices?.length) {
    return `
      <label class="control-field">
        <span>${escapeHtml(control.label)}</span>
        <input value="${escapeHtml(control.current || "")}" disabled />
      </label>
    `;
  }

  const selectedChoice =
    control.choices.find((choice) => choice.label === control.current) ||
    control.choices.find((choice) => choice.value === control.current);

  return `
    <label class="control-field">
      <span>${escapeHtml(control.label)}</span>
      <select data-config-key="${control.key}" ${control.readonly ? "disabled" : ""}>
        ${control.choices
          .map(
            (choice) => `
              <option value="${escapeHtml(choice.value)}" ${
                selectedChoice?.value === choice.value ? "selected" : ""
              }>
                ${escapeHtml(choice.label)}
              </option>
            `,
          )
          .join("")}
      </select>
    </label>
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

async function refreshCameraConfig() {
  const data = await api("/camera/config");
  renderCameraConfig(data.controls);
  if (data.status) renderStatus(data.status);
  return data.status || {};
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

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

els.detectButton.addEventListener("click", () => runAction(() => post("/detect")));
els.startButton.addEventListener("click", () => runAction(() => post("/preview/start")));
els.stopButton.addEventListener("click", () => runAction(() => post("/preview/stop")));
els.captureButton.addEventListener("click", () => runAction(() => post("/capture")));
els.recoverButton.addEventListener("click", () => runAction(() => post("/recover")));
els.refreshConfigButton.addEventListener("click", () => runAction(refreshCameraConfig));
els.autofocusButton.addEventListener("click", () => runAction(() => post("/focus/autofocus")));

document.querySelectorAll("[data-focus-step]").forEach((button) => {
  button.addEventListener("click", () => {
    runAction(() => post("/focus/manual-step", { value: Number(button.dataset.focusStep) }));
  });
});

refreshStatus();
refreshCameraConfig().catch((error) => {
  els.errorValue.textContent = error.message;
});
setInterval(refreshStatus, statusPollMs);
