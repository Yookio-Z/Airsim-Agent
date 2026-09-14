// ui-camera.js —— 摄像头画面与感知叠加
// 由 app.js 拆分而来；各文件共享同一份脚本作用域，按顺序加载。

function mergeVehicleSetupSnapshot(snapshot, options = {}) {
  if (!snapshot || typeof snapshot !== "object") return vehicleSetupCache;
  const previous = vehicleSetupCache && typeof vehicleSetupCache === "object" ? vehicleSetupCache : {};
  const isBusy = snapshot.status === "busy";
  const disconnected = snapshot.connected === false || snapshot.status === "disconnected";

  if (isBusy) {
    vehicleSetupCache = {
      ...previous,
      status: snapshot.status,
      message: snapshot.message || previous.message,
      backend: snapshot.backend || previous.backend,
      connected: previous.connected ?? snapshot.connected,
      updated_at: previous.updated_at,
    };
    return vehicleSetupCache;
  }

  if (disconnected || options.replace) {
    vehicleSetupCache = { ...snapshot };
  } else {
    vehicleSetupCache = {
      ...previous,
      ...snapshot,
      connection: snapshot.connection || previous.connection,
      firmware: snapshot.firmware || previous.firmware,
      parameters: snapshot.parameters || previous.parameters,
      parameter_groups: snapshot.parameter_groups || previous.parameter_groups,
      parameter_highlights: snapshot.parameter_highlights || previous.parameter_highlights,
      summary: snapshot.summary || previous.summary,
      telemetry: snapshot.telemetry || previous.telemetry,
      history: snapshot.history && Object.keys(snapshot.history).length
        ? { ...(previous.history || {}), ...snapshot.history }
        : previous.history,
      read_only: snapshot.read_only || previous.read_only,
    };
  }

  if (vehicleSetupCache?.parameters) {
    vehicleParametersCache = {
      ...(vehicleParametersCache || {}),
      ...vehicleSetupCache.parameters,
      parameters: vehicleParametersCache?.parameters || [],
    };
  }
  if (vehicleInfoCache && typeof vehicleInfoCache === "object") {
    if (vehicleSetupCache.connection) vehicleInfoCache.connection = vehicleSetupCache.connection;
    if (vehicleSetupCache.firmware) vehicleInfoCache.firmware = vehicleSetupCache.firmware;
    if (vehicleSetupCache.parameters) vehicleInfoCache.parameters = vehicleSetupCache.parameters;
    vehicleInfoCache.connected = Boolean(vehicleSetupCache.connected);
    vehicleInfoCache.backend = vehicleSetupCache.backend || vehicleInfoCache.backend;
  }
  return vehicleSetupCache;
}

function normalizeCameraSettings(raw = {}) {
  const imageType = String(raw.image_type || DEFAULT_CAMERA_SETTINGS.image_type).toLowerCase();
  const timeout = Number(raw.timeout_sec || DEFAULT_CAMERA_SETTINGS.timeout_sec);
  return {
    source: String(raw.source || DEFAULT_CAMERA_SETTINGS.source).trim().toLowerCase() || DEFAULT_CAMERA_SETTINGS.source,
    url: String(raw.url || "").trim(),
    camera_name: String(raw.camera_name || DEFAULT_CAMERA_SETTINGS.camera_name).trim() || DEFAULT_CAMERA_SETTINGS.camera_name,
    vehicle_name: String(raw.vehicle_name || "").trim(),
    image_type: ["scene", "depth", "segmentation", "infrared"].includes(imageType) ? imageType : DEFAULT_CAMERA_SETTINGS.image_type,
    timeout_sec: Math.max(3, Math.min(120, Number.isFinite(timeout) ? timeout : DEFAULT_CAMERA_SETTINGS.timeout_sec)),
    auto_save: Boolean(raw.auto_save),
  };
}

async function loadCameraSettings(force = false) {
  if (cameraSettingsLoaded && !force) {
    renderCameraSettings();
    return cameraSettings;
  }
  try {
    const data = await api("/api/settings/camera");
    cameraSettings = normalizeCameraSettings(data.camera || {});
    cameraSettingsLoaded = true;
  } catch (error) {
    showNotice("摄像头设置加载失败: " + (error.message || "未知错误"), "error");
  }
  renderCameraSettings();
  renderCameraMeta();
  return cameraSettings;
}

function readCameraSettingsForm() {
  const source = els.cameraSource?.value || cameraSettings.source;
  return normalizeCameraSettings({
    source: source,
    url: els.cameraRtspUrl?.value || cameraSettings.url || "",
    camera_name: els.cameraName?.value || cameraSettings.camera_name,
    vehicle_name: els.cameraVehicle?.value || "",
    image_type: els.cameraImageType?.value || cameraSettings.image_type,
    timeout_sec: els.cameraTimeout?.value || cameraSettings.timeout_sec,
    auto_save: Boolean(els.cameraAutoSave?.checked),
  });
}





async function saveCameraSettings({ silent = false } = {}) {
  cameraSettings = readCameraSettingsForm();
  renderCameraMeta();
  try {
    const data = await post("/api/settings/camera", cameraSettings);
    cameraSettings = normalizeCameraSettings(data.camera || cameraSettings);
    cameraSettingsLoaded = true;
    renderCameraSettings();
    renderCameraMeta();
    if (!silent) showNotice("摄像头设置已保存", "success");
    return true;
  } catch (error) {
    showNotice("保存摄像头设置失败: " + (error.message || "未知错误"), "error");
    return false;
  }
}









function setupCameraViewerDrag() {
  const handle = els.cameraViewerDragHandle;
  if (!handle || !els.cameraViewer) return;
  let drag = null;

  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("button, select, input")) return;
    const stage = els.cameraViewer.offsetParent;
    if (!stage) return;
    const viewerRect = els.cameraViewer.getBoundingClientRect();
    const stageRect = stage.getBoundingClientRect();
    drag = {
      pointerId: event.pointerId,
      offsetX: event.clientX - viewerRect.left,
      offsetY: event.clientY - viewerRect.top,
      stageRect,
    };
    els.cameraViewer.style.left = `${Math.round(viewerRect.left - stageRect.left)}px`;
    els.cameraViewer.style.top = `${Math.round(viewerRect.top - stageRect.top)}px`;
    els.cameraViewer.style.right = "auto";
    els.cameraViewer.style.bottom = "auto";
    els.cameraViewer.classList.add("dragging");
    handle.setPointerCapture(event.pointerId);
    event.preventDefault();
  });

  handle.addEventListener("pointermove", (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const width = els.cameraViewer.offsetWidth;
    const height = els.cameraViewer.offsetHeight;
    const left = Math.max(6, Math.min(event.clientX - drag.stageRect.left - drag.offsetX, drag.stageRect.width - width - 6));
    const top = Math.max(6, Math.min(event.clientY - drag.stageRect.top - drag.offsetY, drag.stageRect.height - height - 6));
    els.cameraViewer.style.left = `${Math.round(left)}px`;
    els.cameraViewer.style.top = `${Math.round(top)}px`;
    cameraViewerPositioned = true;
  });

  const finishDrag = (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
    drag = null;
    els.cameraViewer.classList.remove("dragging");
  };
  handle.addEventListener("pointerup", finishDrag);
  handle.addEventListener("pointercancel", finishDrag);
}



function cameraSupportsImageCapture() {
  const capabilities = latestState?.tool_runtime?.backend_profile?.capabilities || {};
  return Boolean(capabilities.image_capture);
}

function cameraUnavailableMessage() {
  const runtime = latestState?.tool_runtime || {};
  const backend = backendDisplayName(runtime);
  return `当前 ${backend} 未注册可用图像源，请在系统设置中配置 AirSim 或相机连接`;
}



















function prepareCameraTemplateRoles(el = els.cameraViewer) {
  if (!el) return;
  el.dataset.cameraWindow = el.dataset.cameraWindow || "camera_1";
  el.querySelector("#cameraViewerDragHandle")?.setAttribute("data-camera-role", "handle");
  el.querySelector("#cameraViewerNewWindow")?.setAttribute("data-camera-action", "new");
  el.querySelector("#cameraViewerClose")?.setAttribute("data-camera-action", "close");
  el.querySelector("#cameraViewerSource")?.setAttribute("data-camera-role", "source");
  el.querySelector("#cameraViewerCamera")?.setAttribute("data-camera-role", "camera");
  el.querySelector("#cameraViewerVehicle")?.setAttribute("data-camera-role", "vehicle");
  el.querySelector("#cameraViewerImageType")?.setAttribute("data-camera-role", "imageType");
  el.querySelector("[data-camera-role='camera']")?.closest("label")?.setAttribute("data-camera-role", "cameraField");
  el.querySelector("[data-camera-role='vehicle']")?.closest("label")?.setAttribute("data-camera-role", "vehicleField");
  el.querySelector("[data-camera-role='imageType']")?.closest("label")?.setAttribute("data-camera-role", "imageTypeField");
  el.querySelector("#cameraLiveIndicator")?.setAttribute("data-camera-role", "live");
  el.querySelector("#cameraSnapshotStatus")?.setAttribute("data-camera-role", "status");
  el.querySelector("#cameraImage")?.setAttribute("data-camera-role", "image");
  el.querySelector("#cameraPlaceholder")?.setAttribute("data-camera-role", "placeholder");
  el.querySelector("#cameraMeta")?.setAttribute("data-camera-role", "meta");
}

function stripCameraCloneIds(el) {
  el.removeAttribute("id");
  el.querySelectorAll("[id]").forEach((node) => node.removeAttribute("id"));
}

function ensurePrimaryCameraWindow() {
  if (!els.cameraViewer) return null;
  const existing = cameraWindows.get("camera_1");
  if (existing) return existing;
  prepareCameraTemplateRoles(els.cameraViewer);
  return createCameraWindow({
    id: "camera_1",
    el: els.cameraViewer,
    settings: { ...cameraSettings },
    primary: true,
  });
}

function createCameraWindow({ id = "", el = null, settings = null, primary = false } = {}) {
  if (!el) return null;
  const winId = id || `camera_${++cameraWindowCounter}`;
  el.dataset.cameraWindow = winId;
  el.dataset.streaming = "false";
  el.dataset.state = "idle";
  const win = {
    id: winId,
    primary,
    el,
    ...cameraWindowParts(el),
    settings: normalizeCameraSettings(settings || cameraSettings),
    positioned: false,
    captureInFlight: false,
    streamActive: false,
    timer: null,
    frameSeq: 0,
    objectUrl: "",
    errorCount: 0,
    eventsBound: false,
    lastSuccessSource: "",
  };
  if (!primary && win.imageEl) {
    win.imageEl.removeAttribute("src");
    win.imageEl.hidden = true;
  }
  cameraWindows.set(winId, win);
  syncCameraWindowControls(win);
  renderCameraMeta(null, win);
  setupCameraWindowEvents(win);
  setCameraStreamActive(win, false);
  return win;
}

function primaryCameraWindow() {
  return ensurePrimaryCameraWindow();
}

function visibleCameraWindows() {
  return [...cameraWindows.values()].filter((win) => cameraViewerIsVisible(win));
}

function activeCameraStreams() {
  return [...cameraWindows.values()].filter((win) => win.streamActive && cameraViewerIsVisible(win));
}

function toggleCameraSourceSpecificFields(win) {
  if (!win) return;
  const source = String(win.sourceSelect?.value || win.settings?.source || "airsim").toLowerCase();
  const isAirSim = source === "airsim";
  if (win.cameraFieldEl) win.cameraFieldEl.hidden = !isAirSim;
  if (win.vehicleFieldEl) win.vehicleFieldEl.hidden = !isAirSim;
  if (win.imageTypeFieldEl) win.imageTypeFieldEl.hidden = !isAirSim;
}

function clearCameraWindowImage(win) {
  if (!win) return;
  if (win.objectUrl) {
    try { URL.revokeObjectURL(win.objectUrl); } catch (_) {}
    win.objectUrl = "";
  }
  if (win.imageEl) {
    win.imageEl.removeAttribute("src");
    win.imageEl.hidden = true;
  }
  if (win.placeholderEl) {
    win.placeholderEl.hidden = false;
    win.placeholderEl.textContent = "等待新画面";
  }
}

function syncCameraWindowControls(win) {
  if (!win) return;
  if (win.sourceSelect) win.sourceSelect.value = String(win.settings.source || "airsim").toLowerCase();
  if (win.cameraSelect) {
    const cameraName = String(win.settings.camera_name || "0");
    if (![...win.cameraSelect.options].some((option) => option.value === cameraName)) {
      win.cameraSelect.add(new Option(`${cameraName} · custom`, cameraName));
    }
    win.cameraSelect.value = cameraName;
  }
  if (win.imageTypeSelect) win.imageTypeSelect.value = win.settings.image_type || "scene";
  if (win.vehicleSelect) {
    const vehicleName = String(win.settings.vehicle_name || "");
    const hasOption = [...win.vehicleSelect.options].some((option) => option.value === vehicleName);
    if (vehicleName && !hasOption) {
      win.vehicleSelect.add(new Option(`${vehicleName} · custom`, vehicleName));
    }
    win.vehicleSelect.value = hasOption ? vehicleName : (win.vehicleSelect.value || "");
  }
  toggleCameraSourceSpecificFields(win);
}

function renderCameraSettings() {
  if (els.cameraSource) els.cameraSource.value = cameraSettings.source;
  if (els.cameraName) els.cameraName.value = cameraSettings.camera_name;
  if (els.cameraVehicle) els.cameraVehicle.value = cameraSettings.vehicle_name;
  if (els.cameraImageType) els.cameraImageType.value = cameraSettings.image_type;
  if (els.cameraTimeout) els.cameraTimeout.value = String(Math.round(cameraSettings.timeout_sec));
  if (els.cameraAutoSave) els.cameraAutoSave.checked = Boolean(cameraSettings.auto_save);
  cameraWindows.forEach(syncCameraWindowControls);
}

function cameraSourceLabel(settings) {
  const source = String(settings.source || "airsim").toLowerCase();
  if (source === "airsim") return "AirSim";
  if (source === "local") return "本机摄像头";
  if (source === "rtsp") {
    const url = String(settings.url || "").trim();
    return url ? `RTSP ${url.length > 28 ? url.slice(0, 27) + "…" : url}` : "RTSP";
  }
  return source;
}

function renderCameraMeta(data = null, win = primaryCameraWindow()) {
  const hudEl = win?.hudEl || els.cameraHud;
  if (!hudEl) return;
  const detections = Array.isArray(data?.detections) ? data.detections : [];
  if (detections.length) {
    hudEl.textContent = detections.map((d) => `🎯 ${d.class} ${Number(d.confidence || 0).toFixed(2)}`).join("\n");
    hudEl.hidden = false;
  } else {
    hudEl.hidden = true;
  }
}
function setCameraViewerVisible(visible, win = primaryCameraWindow()) {
  if (!win?.el) return;
  win.el.hidden = !visible;
  syncCameraToolbarState();
  if (visible) {
    focusCameraWindow(win);
    if (!win.positioned) requestAnimationFrame(() => placeCameraViewerAtDefault(win));
  }
}

function cameraViewerIsVisible(win = primaryCameraWindow()) {
  return Boolean(win?.el && !win.el.hidden);
}

function focusCameraWindow(win) {
  if (!win?.el) return;
  win.el.style.zIndex = String(20 + (Date.now() % 100000));
}

function placeCameraViewerAtDefault(win = primaryCameraWindow()) {
  if (!win?.el || win.el.hidden) return;
  const stage = win.el.offsetParent;
  const profile = els.missionProfile;
  if (!stage || !profile) return;
  const stageRect = stage.getBoundingClientRect();
  const profileRect = profile.getBoundingClientRect();
  const viewerRect = win.el.getBoundingClientRect();
  const visible = visibleCameraWindows();
  const index = Math.max(0, visible.findIndex((item) => item.id === win.id));
  const gap = 10;
  const baseLeft = Math.max(10, profileRect.left - stageRect.left);
  const baseTop = profileRect.top - stageRect.top - viewerRect.height - 8;
  const columns = Math.max(1, Math.floor((stageRect.width - baseLeft - 10) / Math.max(1, viewerRect.width + gap)));
  const col = index % columns;
  const row = Math.floor(index / columns);
  setCameraWindowPosition(
    win,
    baseLeft + col * (viewerRect.width + gap),
    baseTop - row * (viewerRect.height + gap),
  );
  win.positioned = true;
}

function clampCameraViewerPosition(win = null) {
  const windows = win ? [win] : [...cameraWindows.values()];
  windows.forEach((item) => {
    if (!item.positioned || !cameraViewerIsVisible(item) || !item.el) return;
    const stage = item.el.offsetParent;
    if (!stage) return;
    const stageRect = stage.getBoundingClientRect();
    const viewerRect = item.el.getBoundingClientRect();
    setCameraWindowPosition(item, viewerRect.left - stageRect.left, viewerRect.top - stageRect.top);
  });
}

function setupCameraWindowDrag(win) {
  const handle = win?.el?.querySelector("header") || win?.handle;
  if (!handle || !win.el) return;
  let drag = null;

  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("button, select, input, .cam-resize")) return;
    const stage = win.el.offsetParent;
    if (!stage) return;
    const viewerRect = win.el.getBoundingClientRect();
    const stageRect = stage.getBoundingClientRect();
    drag = {
      pointerId: event.pointerId,
      offsetX: event.clientX - viewerRect.left,
      offsetY: event.clientY - viewerRect.top,
      stageRect,
    };
    win.el.style.left = `${Math.round(viewerRect.left - stageRect.left)}px`;
    win.el.style.top = `${Math.round(viewerRect.top - stageRect.top)}px`;
    win.el.style.right = "auto";
    win.el.style.bottom = "auto";
    win.el.classList.add("dragging");
    focusCameraWindow(win);
    handle.setPointerCapture(event.pointerId);
    event.preventDefault();
  });

  handle.addEventListener("pointermove", (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    setCameraWindowPosition(
      win,
      event.clientX - drag.stageRect.left - drag.offsetX,
      event.clientY - drag.stageRect.top - drag.offsetY,
    );
    win.positioned = true;
  });

  const finishDrag = (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
    drag = null;
    win.el.classList.remove("dragging");
  };
  handle.addEventListener("pointerup", finishDrag);
  handle.addEventListener("pointercancel", finishDrag);
}

function setCameraViewerState(win, state, message) {
  if (!win?.el) return;
  win.el.dataset.state = state;
  if (win.statusEl) win.statusEl.textContent = message || "";
  const hasFrame = Boolean(win.imageEl?.src);
  if (win.placeholderEl) {
    win.placeholderEl.hidden = state === "ready" || hasFrame;
    win.placeholderEl.textContent = message || "暂无画面";
  }
  if (win.imageEl) win.imageEl.hidden = state !== "ready" && !hasFrame;
}

function setCameraStreamActive(win, active) {
  if (!win) return;
  const next = Boolean(active);
  win.streamActive = next;
  if (!win.streamActive) win.frameSeq += 1;
  if (win.timer) {
    clearTimeout(win.timer);
    win.timer = null;
  }
  if (win.el) win.el.dataset.streaming = String(next);
  if (win.liveIndicator) {
    win.liveIndicator.innerHTML = next ? "<i></i>LIVE" : "<i></i>PAUSED";
  }
  if (!next) {
    const hasFrame = Boolean(win.imageEl?.src);
    setCameraViewerState(win, hasFrame ? "paused" : "idle", hasFrame ? "视频流已暂停" : "未连接视频流");
  }
}

function cameraStreamQuality(source = "airsim") {
  const count = Math.max(1, activeCameraStreams().length);
  // 本机摄像头: 不需要降频, 保持原生画质 + 短轮询间隔, 画面才流畅
  if (String(source || "").toLowerCase() === "local") {
    if (count <= 1) return { interval: 50, maxWidth: 1280, quality: 90 };
    if (count <= 2) return { interval: 70, maxWidth: 960, quality: 86 };
    return { interval: 90, maxWidth: 800, quality: 82 };
  }
  // AirSim / RTSP: 适度降频避免对仿真器/网络造成压力
  if (count <= 1) return { interval: 700, maxWidth: 560, quality: 54 };
  if (count === 2) return { interval: 220, maxWidth: 480, quality: 50 };
  return { interval: 420, maxWidth: 400, quality: 46 };
}

function scheduleCameraFrame(win, delay = null) {
  if (!win?.streamActive || !cameraViewerIsVisible(win)) return;
  if (win.timer) clearTimeout(win.timer);
  const source = win.sourceSelect?.value || win.settings?.source || "airsim";
  const quality = cameraStreamQuality(source);
  const wait = delay ?? (win.el?.dataset.state === "error" ? CAMERA_STREAM_ERROR_INTERVAL_MS : quality.interval);
  win.timer = setTimeout(() => {
    win.timer = null;
    captureCameraFrame({ notify: false, openViewer: false, windowId: win.id });
  }, wait);
}

function readCameraViewerSettings(win = primaryCameraWindow()) {
  return normalizeCameraSettings({
    ...(win?.settings || cameraSettings),
    source: win?.sourceSelect?.value || win?.settings?.source || cameraSettings.source,
    camera_name: win?.cameraSelect?.value || win?.settings?.camera_name || cameraSettings.camera_name,
    vehicle_name: win?.vehicleSelect?.value || win?.settings?.vehicle_name || cameraSettings.vehicle_name,
    image_type: win?.imageTypeSelect?.value || win?.settings?.image_type || cameraSettings.image_type,
  });
}

function cameraPreviewUrl(settings) {
  const source = String(settings.source || "airsim").toLowerCase();
  const quality = cameraStreamQuality(source);
  const params = new URLSearchParams({
    source,
    timeout_sec: String(Math.min(Number(settings.timeout_sec || 2), 2.5)),
    max_width: String(quality.maxWidth),
    quality: String(quality.quality),
    _: String(Date.now()),
  });
  if (source === "airsim") {
    params.set("camera_name", settings.camera_name || "0");
    params.set("vehicle_name", settings.vehicle_name || "");
    params.set("image_type", settings.image_type || "scene");
    params.set("detect", "1");
  }
  return `/api/camera/preview?${params.toString()}`;
}

function cameraWindowById(windowId) {
  return cameraWindows.get(windowId) || primaryCameraWindow();
}

async function captureCameraFrame({ notify = true, openViewer = true, windowId = "camera_1" } = {}) {
  const win = cameraWindowById(windowId);
  if (!win || win.captureInFlight) return;
  if (openViewer) setCameraViewerVisible(true, win);
  if (!cameraSettingsLoaded) await loadCameraSettings();
  win.settings = readCameraViewerSettings(win);
  syncCameraWindowControls(win);
  renderCameraMeta(null, win);

  if (!["airsim", "rtsp", "local"].includes(win.settings.source)) {
    const message = `暂未接入 ${win.settings.source} 图像源`;
    setCameraViewerState(win, "error", message);
    setCameraStreamActive(win, false);
    if (notify) showNotice(message, "error");
    return;
  }

  win.captureInFlight = true;
  const seq = ++win.frameSeq;
  const buttons = [els.cameraCaptureFromSettingsBtn].filter(Boolean);
  buttons.forEach((button) => { button.disabled = true; });
  if (!win.imageEl?.src) setCameraViewerState(win, "loading", "正在连接视频流");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3600);
  try {
    const response = await fetch(cameraPreviewUrl(win.settings), { cache: "no-store", signal: controller.signal });
    if (!response.ok) {
      let message = response.statusText || "获取摄像头画面失败";
      try {
        const data = await response.json();
        message = extractApiError(data, message);
      } catch (_) {
        try {
          message = (await response.text()) || message;
        } catch (_) {}
      }
      throw new Error(message);
    }
    const metaText = response.headers.get("X-Camera-Meta") || "";
    let data = {};
    if (metaText) {
      try { data = JSON.parse(metaText); } catch (_) { data = {}; }
    }
    const blob = await response.blob();
    if (!blob.size) throw new Error("未返回图像数据");
    const nextUrl = URL.createObjectURL(blob);
    if (seq !== win.frameSeq) {
      URL.revokeObjectURL(nextUrl);
      return;
    }
    if (win.imageEl) {
      const previousUrl = win.objectUrl;
      win.objectUrl = nextUrl;
      win.imageEl.src = nextUrl;
      win.imageEl.hidden = false;
      if (previousUrl) setTimeout(() => URL.revokeObjectURL(previousUrl), 250);
    }
    win.errorCount = 0;
    // 视频流正常时不显示时间戳, 保持头部紧凑; 仅在非流式(单帧)时给出提示
    setCameraViewerState(win, "ready", win.streamActive ? "" : (data.message || "画面已更新"));
    renderCameraMeta(data, win);
    win.lastSuccessSource = win.settings.source;
    if (notify) showNotice(data.message || "摄像头画面已更新", "success");
  } catch (error) {
    win.errorCount += 1;
    const message = error.name === "AbortError" ? "摄像头预览超时，正在降频重试" : (error.message || "获取摄像头画面失败");
    setCameraViewerState(win, "error", message);
    if (notify || win.errorCount === 1) showNotice(message, "error");
    // 持续失败达到阈值后停止轮询, 避免对已断开/不可达的源做无效重试 (例如 AirSim 端口未开)
    if (win.errorCount >= MAX_CAMERA_STREAM_ERRORS && win.streamActive) {
      setCameraStreamActive(win, false);
      setCameraViewerState(win, "error", `${message}（已停止重试，切换图像源或关闭后重开）`);
      showNotice("已自动停止摄像头重试", "warning");
    }
  } finally {
    clearTimeout(timeout);
    win.captureInFlight = false;
    buttons.forEach((button) => { button.disabled = false; });
    if (win.streamActive) scheduleCameraFrame(win);
  }
}

async function startCameraStream({ notify = true, windowId = "camera_1", settings = null } = {}) {
  const win = cameraWindowById(windowId);
  if (!win) return;
  if (settings) {
    win.settings = normalizeCameraSettings(settings);
    syncCameraWindowControls(win);
  }
  setCameraViewerVisible(true, win);
  await loadCameraSettings();
  setCameraStreamActive(win, true);
  await captureCameraFrame({ notify, openViewer: false, windowId: win.id });
}

function stopCameraStream({ hide = false, windowId = "camera_1" } = {}) {
  const win = cameraWindowById(windowId);
  if (!win) return;
  setCameraStreamActive(win, false);
  if (hide && win.objectUrl) {
    URL.revokeObjectURL(win.objectUrl);
    win.objectUrl = "";
    if (win.imageEl) win.imageEl.removeAttribute("src");
  }
  if (hide) {
    setCameraViewerVisible(false, win);
    if (!win.primary && win.el) {
      win.el.remove();
      cameraWindows.delete(win.id);
    }
  } else if (cameraViewerIsVisible(win)) {
    setCameraViewerState(win, win.imageEl?.src ? "ready" : "idle", "视频流已暂停");
  }
  syncCameraToolbarState();
}

function stopAllCameraStreams({ hide = false } = {}) {
  [...cameraWindows.values()].forEach((win) => stopCameraStream({ hide, windowId: win.id }));
}

function nextCameraName() {
  const used = new Set(visibleCameraWindows().map((win) => String(win.settings.camera_name || "0")));
  for (let index = 0; index <= 4; index += 1) {
    const name = String(index);
    if (!used.has(name)) return name;
  }
  return "0";
}

async function createAdditionalCameraWindow(sourceWindow = primaryCameraWindow()) {
  if (visibleCameraWindows().length >= MAX_CAMERA_WINDOWS) {
    showNotice(`最多同时打开 ${MAX_CAMERA_WINDOWS} 个摄像头窗口`, "error");
    return null;
  }
  prepareCameraTemplateRoles(els.cameraViewer);
  const clone = els.cameraViewer.cloneNode(true);
  stripCameraCloneIds(clone);
  clone.hidden = true;
  clone.classList.remove("dragging");
  clone.dataset.cameraWindow = "";
  els.cameraViewer.parentElement.appendChild(clone);
  const win = createCameraWindow({
    el: clone,
    settings: {
      ...(sourceWindow?.settings || cameraSettings),
      camera_name: nextCameraName(),
    },
    primary: false,
  });
  await startCameraStream({ notify: false, windowId: win.id });
  return win;
}

async function updateCameraViewerSelection(eventOrWindow = null) {
  const win = eventOrWindow?.el
    ? eventOrWindow
    : cameraWindowById(eventOrWindow?.target?.closest?.(".camera-viewer")?.dataset.cameraWindow || "camera_1");
  if (!win) return;
  win.settings = readCameraViewerSettings(win);
  if (win.primary) {
    cameraSettings = { ...win.settings };
    renderCameraSettings();
    await saveCameraSettings({ silent: true });
  }
  renderCameraMeta(null, win);
  if (cameraViewerIsVisible(win)) {
    if (win.settings.source !== (win.lastSuccessSource || "")) {
      clearCameraWindowImage(win);
    }
    setCameraViewerState(win, "loading", "正在切换图像源");
    if (!win.streamActive) setCameraStreamActive(win, true);
    await captureCameraFrame({ notify: false, openViewer: false, windowId: win.id });
  }
}

function resetCameraViewerSize(win = primaryCameraWindow()) {
  const viewer = win?.el || els.cameraViewer;
  if (!viewer) return;
  viewer.style.removeProperty("width");
  viewer.style.removeProperty("height");
  viewer.style.removeProperty("left");
  viewer.style.removeProperty("top");
  viewer.style.removeProperty("right");
  viewer.style.removeProperty("bottom");
}

function bindCameraViewerResize() {
  if (window.__camResizeBound) return;
  window.__camResizeBound = true;
  document.addEventListener("pointerdown", (e) => {
    const edge = e.target && e.target.closest ? e.target.closest(".cam-resize") : null;
    if (!edge || e.button !== 0) return;
    const viewer = edge.closest(".camera-viewer");
    if (!viewer) return;
    const stage = viewer.offsetParent;
    if (!stage) return;
    e.preventDefault();
    e.stopPropagation();
    const dir = edge.dataset.cameraResize || "se";
    const viewerRect = viewer.getBoundingClientRect();
    const stageRect = stage.getBoundingClientRect();
    // 全部换算到 offsetParent(舞台) 局部坐标, 与拖拽保持一致
    const startLeft = viewerRect.left - stageRect.left;
    const startTop = viewerRect.top - stageRect.top;
    const startW = viewerRect.width;
    const startH = viewerRect.height;
    const startX = e.clientX;
    const startY = e.clientY;
    const minW = 300;
    const minH = 220;
    viewer.style.transition = "none";
    viewer.style.right = "auto";
    viewer.style.bottom = "auto";
    viewer.style.left = `${Math.round(startLeft)}px`;
    viewer.style.top = `${Math.round(startTop)}px`;
    viewer.style.width = `${Math.round(startW)}px`;
    viewer.style.height = `${Math.round(startH)}px`;
    viewer.classList.add("resizing");
    function onMove(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      const right = startLeft + startW;
      const bottom = startTop + startH;
      let left = startLeft;
      let top = startTop;
      let w = startW;
      let h = startH;
      if (dir.includes("e")) w = Math.max(minW, startW + dx);
      if (dir.includes("s")) h = Math.max(minH, startH + dy);
      if (dir.includes("w")) {
        left = Math.min(right - minW, startLeft + dx);
        left = Math.max(0, left);
        w = right - left;
      }
      if (dir.includes("n")) {
        top = Math.min(bottom - minH, startTop + dy);
        top = Math.max(0, top);
        h = bottom - top;
      }
      w = Math.min(w, stageRect.width - left);
      h = Math.min(h, stageRect.height - top);
      viewer.style.left = `${Math.round(left)}px`;
      viewer.style.top = `${Math.round(top)}px`;
      viewer.style.width = `${Math.round(w)}px`;
      viewer.style.height = `${Math.round(h)}px`;
    }
    function onUp() {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      viewer.classList.remove("resizing");
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  });
}




function renderImagePreview() {
  if (!els.imagePreview) return;
  els.imagePreview.hidden = pendingImages.length === 0;
  els.imagePreview.innerHTML = pendingImages.map((item, index) => `
    <figure class="composer-image-preview">
      <img src="${escapeHtml(item.data_url)}" alt="${escapeHtml(item.name)}">
      <button type="button" data-remove-image="${index}" title="移除图片">×</button>
      <figcaption>${escapeHtml(item.name)}</figcaption>
    </figure>
  `).join("");
}

function syncCameraVehicleOptions(vehicles) {
  const list = Array.isArray(vehicles) ? vehicles : [];
  const names = list.map((vehicle) => String(vehicle?.vehicle_name || "").trim()).filter(Boolean);
  cameraWindows.forEach((win) => {
    if (!win.vehicleSelect) return;
    const previous = win.vehicleSelect.value || win.settings?.vehicle_name || "";
    win.vehicleSelect.textContent = "";
    if (!names.length) {
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "等待 AirSim";
      win.vehicleSelect.appendChild(placeholder);
    } else {
      names.forEach((name) => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        win.vehicleSelect.appendChild(option);
      });
    }
    if (previous && names.includes(previous)) {
      win.vehicleSelect.value = previous;
    } else if (names.length) {
      win.vehicleSelect.value = names[0];
    }
  });
}



function setupSnapshot() {
  return vehicleSetupCache && typeof vehicleSetupCache === "object" ? vehicleSetupCache : {};
}

function renderVehicleAirframePanel() {
  const panel = els.vehicleAirframePanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const setup = setupSnapshot();
  const airframe = setup.summary?.airframe || {};
  const firmware = setup.firmware || {};
  const link = setup.connection || {};
  panel.innerHTML = `
    <div class="airframe-layout">
      <section class="setup-detail-card">
        <div class="quad-visual" aria-hidden="true">
          <span class="quad-arm arm-a"></span><span class="quad-arm arm-b"></span>
          <span class="quad-body"></span>
          <span class="quad-rotor r1"></span><span class="quad-rotor r2"></span><span class="quad-rotor r3"></span><span class="quad-rotor r4"></span>
        </div>
        ${setupRows([
          ["系统 ID", airframe.system_id || link.system_id],
          ["组件 ID", link.component_id],
          ["机型", airframe.vehicle_type],
          ["Autopilot", airframe.autopilot],
          ["SYS_AUTOSTART", airframe.autostart],
          ["SYS_AUTOCONFIG", airframe.airframe_id],
        ])}
      </section>
      <section class="setup-detail-card">
        <strong>固件识别</strong>
        ${setupRows([
          ["PX4 版本", firmwareVersionText(firmware)],
          ["Vendor/Product", `${valueText(firmware.vendor_id)} / ${valueText(firmware.product_id)}`],
          ["UID", firmware.uid],
          ["Git hash", firmware.git_hash],
          ["MAVLink", link.mavlink_wire_protocol],
        ])}
      </section>
    </div>
    ${dataSourceRibbon(["HEARTBEAT", "AUTOPILOT_VERSION", "PARAM_VALUE"])}
    ${readOnlyRibbon()}
  `;
}

