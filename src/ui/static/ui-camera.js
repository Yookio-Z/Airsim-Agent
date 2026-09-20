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
  const transport = String(raw.transport || DEFAULT_CAMERA_SETTINGS.transport).toLowerCase();
  return {
    source: String(raw.source || DEFAULT_CAMERA_SETTINGS.source).trim().toLowerCase() || DEFAULT_CAMERA_SETTINGS.source,
    url: String(raw.url || "").trim(),
    transport: ["tcp", "udp"].includes(transport) ? transport : DEFAULT_CAMERA_SETTINGS.transport,
    camera_name: String(raw.camera_name || DEFAULT_CAMERA_SETTINGS.camera_name).trim() || DEFAULT_CAMERA_SETTINGS.camera_name,
    vehicle_name: String(raw.vehicle_name || "").trim(),
    image_type: ["scene", "depth", "segmentation", "infrared"].includes(imageType) ? imageType : DEFAULT_CAMERA_SETTINGS.image_type,
    timeout_sec: Math.max(3, Math.min(120, Number.isFinite(timeout) ? timeout : DEFAULT_CAMERA_SETTINGS.timeout_sec)),
    auto_save: Boolean(raw.auto_save),
  };
}

// 每种图像源只有一部分字段有意义：AirSim 才有相机 ID/车辆名/图像类型，
// RTSP 才有 URL 和传输协议，本机摄像头只有索引。全铺出来会让操作员
// 以为"填了就会生效"（例如给 RTSP 填图像类型）。
function updateCameraSourceFields() {
  const source = String(els.cameraSource?.value || cameraSettings.source || "airsim").toLowerCase();
  const isAirSim = source === "airsim";
  const isRtsp = source === "rtsp";
  const isLocal = source === "local";
  if (els.cameraRtspUrlRow) els.cameraRtspUrlRow.hidden = !isRtsp;
  if (els.cameraRtspTransportRow) els.cameraRtspTransportRow.hidden = !isRtsp;
  if (els.cameraNameRow) els.cameraNameRow.hidden = !(isAirSim || isLocal);
  if (els.cameraVehicleRow) els.cameraVehicleRow.hidden = !isAirSim;
  if (els.cameraImageTypeRow) els.cameraImageTypeRow.hidden = !isAirSim;
  if (els.cameraNameRow) {
    const label = els.cameraNameRow.querySelector("label");
    if (label) label.textContent = isLocal ? "摄像头索引" : "相机 ID";
    if (els.cameraName) {
      els.cameraName.placeholder = isLocal ? "0" : "0";
    }
  }
  const hint = els.cameraSourceHint;
  if (hint) {
    if (isRtsp) {
      hint.hidden = false;
      hint.textContent = "连不上或画面花屏时换另一种传输协议再试：TCP 走可靠传输更稳，UDP 延迟更低。改完点「保存」，再点「查看画面」验证。";
    } else if (isLocal) {
      hint.hidden = false;
      hint.textContent = "本机摄像头用于在地面端验证视频链路，不参与真机图传。";
    } else {
      hint.hidden = true;
      hint.textContent = "";
    }
  }
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
    transport: els.cameraRtspTransport?.value || cameraSettings.transport,
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
  el.querySelector("#cameraDetectStatus")?.setAttribute("data-camera-role", "detectStatus");
  el.querySelector("#cameraDetectToggle")?.setAttribute("data-camera-role", "detectToggle");
  el.querySelector("#cameraPerf")?.setAttribute("data-camera-role", "perf");
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
    detectStatusEl: el.querySelector("[data-camera-role='detectStatus']"),
    detectToggleEl: el.querySelector("[data-camera-role='detectToggle']"),
    settings: normalizeCameraSettings(settings || cameraSettings),
    positioned: false,
    captureInFlight: false,
    streamActive: false,
    timer: null,
    frameSeq: 0,
    frameTimes: [],
    lastFrameTimestamp: null,
    lastFrameAt: 0,
    axisFrameAgeS: null,
    previewCached: false,
    objectUrl: "",
    staleObjectUrls: [],
    liveBadgeKey: "",
    detectBadgeKey: "",
    perfKey: "",
    statusKey: "",
    detectErrorMessage: "",
    detectErrorUntil: 0,
    errorCount: 0,
    eventsBound: false,
    lastSuccessSource: "",
  };
  if (!primary && win.imageEl) {
    win.imageEl.removeAttribute("src");
    win.imageEl.hidden = true;
  }
  if (!primary && win.perfEl) {
    // 新窗口是从主面板克隆出来的，会把主面板当时的帧率文字一起带过来；
    // 它自己还没抓到帧，先清空，等第一帧到了再按自己的数据填。
    win.perfEl.textContent = "";
    win.perfEl.hidden = true;
  }
  cameraWindows.set(winId, win);
  syncCameraWindowControls(win);
  renderCameraMeta(null, win);
  setupCameraWindowEvents(win);
  win.detectToggleEl?.addEventListener("click", () => toggleCameraDetection());
  setCameraStreamActive(win, false);
  renderCameraDetection(win);
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

// -- object URL 生命周期 -------------------------------------------------
// 旧帧的 URL 不能按定时器释放：新图解码偶发变慢时，到点的 revoke 会把还在
// 显示的旧图撤掉，<img> 一空就是一次可见的闪黑。约定是"新图 onload/onerror 后
// 再释放上一张"，窗口里用 staleObjectUrls 暂存等待释放的 URL。
function releaseCameraStaleObjectUrls(win) {
  if (!win || !win.staleObjectUrls?.length) return;
  const urls = win.staleObjectUrls;
  win.staleObjectUrls = [];
  urls.forEach((url) => {
    if (url && url !== win.objectUrl) {
      try { URL.revokeObjectURL(url); } catch (_) {}
    }
  });
}

function revokeCameraObjectUrls(win) {
  if (!win) return;
  const urls = [...(win.staleObjectUrls || []), win.objectUrl].filter(Boolean);
  win.staleObjectUrls = [];
  win.objectUrl = "";
  urls.forEach((url) => {
    try { URL.revokeObjectURL(url); } catch (_) {}
  });
}

function clearCameraWindowImage(win) {
  if (!win) return;
  revokeCameraObjectUrls(win);
  // 换源后旧源的帧率样本作废，否则徽标会拿上一个源的残留帧算出假帧率
  win.frameTimes = [];
  win.lastFrameTimestamp = null;
  win.lastFrameAt = 0;
  win.axisFrameAgeS = null;
  win.previewCached = false;
  win.liveBadgeKey = "";
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
    if (vehicleName && ![...win.vehicleSelect.options].some((option) => option.value === vehicleName)) {
      win.vehicleSelect.add(new Option(`${vehicleName} · custom`, vehicleName));
    }
    // 补选项和选中要在同一次同步里完成（原先 hasOption 是补之前算的，
    // 新加的 custom 项要等下一次轮询才被选中，面板上就是标签来回跳）
    win.vehicleSelect.value = vehicleName || win.vehicleSelect.value || "";
  }
  toggleCameraSourceSpecificFields(win);
}

function renderCameraSettings() {
  if (els.cameraSource) els.cameraSource.value = cameraSettings.source;
  if (els.cameraRtspUrl) els.cameraRtspUrl.value = cameraSettings.url || "";
  if (els.cameraRtspTransport) els.cameraRtspTransport.value = cameraSettings.transport || DEFAULT_CAMERA_SETTINGS.transport;
  if (els.cameraName) els.cameraName.value = cameraSettings.camera_name;
  if (els.cameraVehicle) els.cameraVehicle.value = cameraSettings.vehicle_name;
  if (els.cameraImageType) els.cameraImageType.value = cameraSettings.image_type;
  if (els.cameraTimeout) els.cameraTimeout.value = String(Math.round(cameraSettings.timeout_sec));
  if (els.cameraAutoSave) els.cameraAutoSave.checked = Boolean(cameraSettings.auto_save);
  updateCameraSourceFields();
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

// -- 帧率徽标 ---------------------------------------------------------
// 固定显示在面板控制条里，不再烧录进画面：画面是 object-fit: cover 铺满画面区的，
// 烧在图片左上角的字在小面板上会被裁掉，只有放大面板才看得见。
// 数值优先用感知轴 meta 报的 fps/detect_fps（服务端近 3 秒的真实采集/检测速率）；
// 本机摄像头 / RTSP / 深度图这些没有感知轴的源，就用本面板自己收到的帧率兜底。
const CAMERA_PERF_WINDOW_MS = 3000;

function noteCameraFrameArrival(win, meta = {}) {
  if (!win) return false;
  const stamp = Number(meta.frame_timestamp);
  const identified = Number.isFinite(stamp) && stamp > 0;
  win.previewCached = meta.preview_cached === true || Number.isFinite(meta.axis_frame_age_s);
  // 服务端报的帧龄每次轮询都更新（哪怕 timestamp 没变），状态徽标要用它
  const age = Number(meta.axis_frame_age_s);
  win.axisFrameAgeS = Number.isFinite(age) ? Math.max(0, age) : null;
  if (identified && stamp === win.lastFrameTimestamp) return false;
  win.lastFrameTimestamp = identified ? stamp : null;
  const now = Date.now();
  win.lastFrameAt = now;
  const times = (win.frameTimes || []).filter((ts) => now - ts <= CAMERA_PERF_WINDOW_MS);
  times.push(now);
  win.frameTimes = times;
  return true;
}

function cameraDeliveryFps(win) {
  const times = (win?.frameTimes || []).filter((ts) => Date.now() - ts <= CAMERA_PERF_WINDOW_MS);
  if (times.length < 2) return 0;
  if (times.length >= 3) {
    // 用中位帧间隔估算。切回标签页/面板重新可见时会立刻补一次抓帧，按"帧数 ÷ 跨度"
    // 算会把这种突发读成帧率翻倍（实测 1.4/s 的 AirSim 流被读成 3.0）。
    const gaps = [];
    for (let i = 1; i < times.length; i += 1) gaps.push(times[i] - times[i - 1]);
    gaps.sort((a, b) => a - b);
    const median = gaps[Math.floor(gaps.length / 2)];
    if (median > 0) return 1000 / median;
  }
  const span = Math.max((Date.now() - times[0]) / 1000, 0.1);
  return (times.length - 1) / span;
}

function renderCameraPerf(win, meta = null) {
  const el = win?.perfEl;
  if (!el) return;
  if (!win.streamActive) {
    win.perfKey = "";
    if (!el.hidden) el.hidden = true;
    return;
  }
  const serverFps = Number(meta?.fps);
  const hasServerFps = Number.isFinite(serverFps) && serverFps > 0;
  const fps = cameraDeliveryFps(win);
  if (!(fps > 0)) {
    // 还没攒够两帧（或流刚停）：先不显示，避免出现 "FPS 0.0" 这种读数
    win.perfKey = "";
    if (!el.hidden) el.hidden = true;
    return;
  }
  // 检测速率与检测徽标/开关同源：都读遥测的 tool_runtime.perception.detect_fps，
  // 只有检测在跑（detect_fps > 0）才显示。预览 meta 里的 detect_fps 是另一套
  // 数据源，预览失败/缓存时会过期，曾出现"未检测"和"检测 1.2/s"同屏打架，
  // 不再采用。
  const detection = cameraDetectionSnapshot();
  const detectFps = detection.running ? Number(detection.detectFps) : NaN;
  const text = Number.isFinite(detectFps) && detectFps > 0
    ? `FPS ${fps.toFixed(1)} · 识别 ${detectFps.toFixed(1)}/s`
    : `FPS ${fps.toFixed(1)}`;
  const title = hasServerFps
    ? `面板新帧刷新率；后台采集 ${serverFps.toFixed(1)} FPS`
    : "面板近 3 秒实际收到的画面帧率";
  // 遥测 250ms 一刷：内容没变就别重写 DOM（重写会让徽标闪一下）
  const key = `${text}\u0000${title}`;
  if (win.perfKey !== key) {
    win.perfKey = key;
    el.innerHTML = text;
    el.title = title;
  }
  if (el.hidden) el.hidden = false;
}

// -- 检测开关与状态徽标 -------------------------------------------------
// 相机流默认自己跑，YOLO 检测由操作员手动开关：状态全部从遥测的
// tool_runtime.perception 派生，detect_fps 为 null/缺省 = 检测未运行。
// 开关标签与动作只看派生的 running（detect_fps > 0）：running = "停止目标检测"，
// 否则 = "开启目标检测"，绝不能用另一套判据，否则会出现"检测在跑但按钮还写着
// 开启"的矛盾。刚点过"开启目标检测"后的模型加载期本地记一个 pending 窗口，
// 只用来让徽标显示"检测启动中"，不拿 enabled 判断——它只说明感知轴/相机流
// 被启用，检测可能已经停了。
// 徽标和开关一起放在面板标题行里（原先在控制条第二行，把面板撑得很挤），
// 与 LIVE 徽标一样，遥测 250ms 一刷，但只有派生出的文案键变化才写 DOM。
const CAMERA_DETECT_PENDING_MS = 20000;
const CAMERA_DETECT_ERROR_MS = 6000;
let cameraDetectionPendingUntil = 0;
let cameraDetectionRequestInFlight = false;

function markCameraDetectionPending(ms = CAMERA_DETECT_PENDING_MS) {
  cameraDetectionPendingUntil = Date.now() + Math.max(0, Number(ms) || 0);
}

function clearCameraDetectionPending() {
  cameraDetectionPendingUntil = 0;
}

function cameraDetectionSnapshot(runtime = null, now = Date.now()) {
  const perception = runtime?.perception || latestState?.tool_runtime?.perception || {};
  const detectFps = Number(perception.detect_fps);
  const running = Number.isFinite(detectFps) && detectFps > 0;
  const starting = !running && now < cameraDetectionPendingUntil;
  const stale = running && perception.detection_stale === true;
  const rawTargets = Number(perception.targets);
  const targetCount = Number.isFinite(rawTargets) && rawTargets >= 0 ? Math.round(rawTargets) : null;
  const parts = [];
  let tone = "idle";
  let title = "目标检测未运行，画面仍会持续更新；点『开启目标检测』启动 YOLO";
  if (running) {
    parts.push(stale ? "检测陈旧" : "检测中");
    parts.push(`${detectFps.toFixed(1)}/s`);
    tone = stale ? "warn" : "live";
    title = stale
      ? `检测线程在跑（${detectFps.toFixed(1)} 次/秒），但最近一次检测结果已过期，可能正在排队或卡住`
      : `后台目标检测运行中：${detectFps.toFixed(1)} 次/秒`;
    if (targetCount != null) {
      parts.push(`${targetCount} 目标`);
      if (!stale) title += `，当前 ${targetCount} 个目标`;
    }
  } else if (starting) {
    parts.push("检测启动中");
    title = "检测服务已启动，YOLO 模型加载可能需要十几秒";
  } else {
    parts.push("未检测");
  }
  return {
    running,
    starting,
    stale,
    targetCount,
    detectFps: running ? detectFps : null,
    label: parts.join(" · "),
    tone,
    title,
    key: `${tone}|${parts.join("|")}`,
  };
}

function renderCameraDetection(win) {
  if (!win) return;
  const statusEl = win.detectStatusEl;
  const toggleEl = win.detectToggleEl;
  if (!statusEl && !toggleEl) return;
  const snapshot = cameraDetectionSnapshot();
  const errorText = win.detectErrorMessage && Date.now() < Number(win.detectErrorUntil || 0)
    ? win.detectErrorMessage
    : "";
  if (statusEl) {
    const key = `${snapshot.key}|${errorText}`;
    if (win.detectBadgeKey !== key) {
      win.detectBadgeKey = key;
      if (errorText) {
        statusEl.dataset.health = "error";
        statusEl.textContent = errorText;
        statusEl.title = errorText;
      } else {
        statusEl.dataset.health = snapshot.tone;
        statusEl.textContent = snapshot.label;
        statusEl.title = snapshot.title;
      }
    }
  }
  if (toggleEl) {
    const busy = cameraDetectionRequestInFlight;
    // 标签必须说清开的是"目标检测"（YOLO），且只跟 running 走：
    // running（detect_fps > 0）= 检测在跑 → 按钮是"停止目标检测"。
    const label = busy ? "处理中…" : (snapshot.running ? "停止目标检测" : "开启目标检测");
    const title = snapshot.running
      ? "停止后台 YOLO 目标检测（画面继续更新）"
      : "启动后台 YOLO 目标检测（模型加载可能需要十几秒）";
    if (toggleEl.textContent !== label) toggleEl.textContent = label;
    if (toggleEl.title !== title) toggleEl.title = title;
    if (toggleEl.disabled !== busy) toggleEl.disabled = busy;
    const detecting = String(snapshot.running);
    if (toggleEl.dataset.detecting !== detecting) toggleEl.dataset.detecting = detecting;
  }
}

async function toggleCameraDetection() {
  if (cameraDetectionRequestInFlight) return;
  const snapshot = cameraDetectionSnapshot();
  // 与按钮标签同一个判据：running 才发停止指令
  const stopping = snapshot.running;
  cameraDetectionRequestInFlight = true;
  cameraWindows.forEach(renderCameraDetection);
  try {
    const result = await post("/api/tool", {
      tool: stopping ? "perception_stop" : "perception_start",
      params: {},
      dry_run: false,
    });
    if (stopping) clearCameraDetectionPending();
    else markCameraDetectionPending();
    cameraWindows.forEach((win) => {
      win.detectErrorMessage = "";
      win.detectErrorUntil = 0;
    });
    showNotice(extractApiSuccess(result, stopping ? "检测已停止" : "检测已启动"), "success");
    refresh().catch(() => {});
  } catch (error) {
    const message = (error && error.message) || "检测指令执行失败";
    cameraWindows.forEach((win) => {
      win.detectErrorMessage = message;
      win.detectErrorUntil = Date.now() + CAMERA_DETECT_ERROR_MS;
    });
  } finally {
    cameraDetectionRequestInFlight = false;
    cameraWindows.forEach(renderCameraDetection);
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

// -- 标题行状态槽 -------------------------------------------------------
// 标题行只放短标签（连接中…/连接失败/已暂停/未连接），完整说明（失败原因 +
// 恢复办法）进 title 提示，并继续写进画面区的占位文字。fetch 失败时浏览器给的
// 是 "Failed to fetch"，再拼上"已停止重试…"整句塞进标题行会把这个槽的基准宽度
// 撑到整行，逼着图像源下拉与检测开关换行、被推着走（截图反馈）。
// 抓帧 0.7s 一次、恢复流程也会调，派生出的标签/提示没变就不写 DOM。
const CAMERA_STATUS_LABELS = {
  loading: "连接中…",
  error: "连接失败",
  paused: "已暂停",
  idle: "未连接",
};

function cameraStatusPresentation(state, message) {
  const detail = String(message || "").trim();
  const label = CAMERA_STATUS_LABELS[state] || detail;
  return { label, title: detail || label };
}

function setCameraViewerState(win, state, message) {
  if (!win?.el) return;
  if (win.el.dataset.state !== state) win.el.dataset.state = state;
  const { label, title } = cameraStatusPresentation(state, message);
  if (win.statusEl) {
    const key = `${label}\u0000${title}`;
    if (win.statusKey !== key) {
      win.statusKey = key;
      if (win.statusEl.textContent !== label) win.statusEl.textContent = label;
      if (win.statusEl.title !== title) win.statusEl.title = title;
    }
  }
  const hasFrame = Boolean(win.imageEl?.src);
  if (win.placeholderEl) {
    const placeholderHidden = state === "ready" || hasFrame;
    if (win.placeholderEl.hidden !== placeholderHidden) win.placeholderEl.hidden = placeholderHidden;
    const placeholderText = String(message || "").trim() || "暂无画面";
    if (win.placeholderEl.textContent !== placeholderText) win.placeholderEl.textContent = placeholderText;
  }
  if (win.imageEl) {
    const imageHidden = state !== "ready" && !hasFrame;
    if (win.imageEl.hidden !== imageHidden) win.imageEl.hidden = imageHidden;
  }
}

// -- 状态徽标 -----------------------------------------------------------
// 画面与飞控链路是解耦的（感知轴有自己的相机源），所以徽标要同时说清两件事：
// 有没有新画面、飞控在不在线。只看 streamActive 会在飞控离线时把正常画面
// 谎报成 LIVE。新鲜度优先用感知轴 meta 的 axis_frame_age_s（服务端权威），
// 没有 meta 的源（本机摄像头/RTSP/旧缓存响应）退回本面板收到的最后一帧时间。
const CAMERA_FRAME_FRESH_MS = 3000;

function cameraFlightLinkOnline(runtime = null) {
  const toolRuntime = runtime || latestState?.tool_runtime || {};
  // 与顶栏 OFF/ONLINE 同款判断：connected 且心跳没过期
  return Boolean(toolRuntime.connected) && !toolRuntime.stale_connection;
}

function cameraFrameFresh(win, now = Date.now()) {
  if (!win) return false;
  if (Number.isFinite(win.axisFrameAgeS)) return win.axisFrameAgeS * 1000 <= CAMERA_FRAME_FRESH_MS;
  return Boolean(win.lastFrameAt) && now - win.lastFrameAt <= CAMERA_FRAME_FRESH_MS;
}

function cameraStreamBadge(win, runtime = null, now = Date.now()) {
  if (!win?.streamActive) return { key: "paused", tone: "idle", label: "PAUSED", title: "视频流已暂停" };
  const link = cameraFlightLinkOnline(runtime);
  if (cameraFrameFresh(win, now)) {
    return link
      ? { key: "live", tone: "live", label: "LIVE", title: "画面实时更新，飞控链路在线" }
      : { key: "frame-link-off", tone: "warn", label: "画面可用 · 飞控离线", title: "画面来自独立相机源；飞控链路离线，起飞/控制不可用" };
  }
  return link
    ? { key: "no-frame", tone: "warn", label: "无画面", title: "最近 3 秒没有收到新画面" }
    : { key: "no-frame-link-off", tone: "warn", label: "无画面 · 飞控离线", title: "最近 3 秒没有新画面，且飞控链路离线" };
}

function renderCameraLiveIndicator(win, runtime = null) {
  const el = win?.liveIndicator;
  if (!el) return;
  const badge = cameraStreamBadge(win, runtime);
  // 遥测 250ms 一刷，内容没变就别重写 DOM（重写 innerHTML 会让圆点闪一下）
  if (win.liveBadgeKey === badge.key) return;
  win.liveBadgeKey = badge.key;
  el.dataset.health = badge.tone;
  el.innerHTML = `<i></i>${badge.label}`;
  el.title = badge.title;
}

function setCameraStreamActive(win, active) {
  if (!win) return;
  const next = Boolean(active);
  win.streamActive = next;
  if (!win.streamActive) {
    win.frameSeq += 1;
    // 流停了就没有新鲜帧；重开时要等第一帧到齐才敢亮 LIVE
    win.lastFrameAt = 0;
    win.axisFrameAgeS = null;
  }
  if (win.timer) {
    clearTimeout(win.timer);
    win.timer = null;
  }
  if (win.el) win.el.dataset.streaming = String(next);
  renderCameraLiveIndicator(win);
  if (!next) {
    const hasFrame = Boolean(win.imageEl?.src);
    setCameraViewerState(win, hasFrame ? "paused" : "idle", hasFrame ? "视频流已暂停" : "未连接视频流");
    renderCameraPerf(win);
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
  // 缓存轮询不会再次调用相机；直接取图仍使用原来的限速。
  const interval = win.previewCached ? 100 * Math.max(1, activeCameraStreams().length) : quality.interval;
  const wait = delay ?? (win.el?.dataset.state === "error" ? CAMERA_STREAM_ERROR_INTERVAL_MS : interval);
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
    if (seq !== win.frameSeq) return;
    win.errorCount = 0;
    if (!noteCameraFrameArrival(win, data)) {
      setCameraViewerState(win, "ready", "");
      renderCameraPerf(win, data);
      return;
    }
    const nextUrl = URL.createObjectURL(blob);
    if (win.imageEl) {
      const previousUrl = win.objectUrl;
      if (previousUrl) (win.staleObjectUrls || (win.staleObjectUrls = [])).push(previousUrl);
      win.objectUrl = nextUrl;
      // 上一张的 URL 等这张 onload/onerror 后再释放（见 releaseCameraStaleObjectUrls）
      win.imageEl.onload = () => releaseCameraStaleObjectUrls(win);
      win.imageEl.onerror = () => releaseCameraStaleObjectUrls(win);
      win.imageEl.src = nextUrl;
      win.imageEl.hidden = false;
    } else {
      URL.revokeObjectURL(nextUrl);
    }
    // 视频流正常时不显示时间戳, 保持头部紧凑; 仅在非流式(单帧)时给出提示
    setCameraViewerState(win, "ready", win.streamActive ? "" : (data.message || "画面已更新"));
    renderCameraMeta(data, win);
    renderCameraPerf(win, data);
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
      // 整句只进 title 与画面区占位文字；标题行由 setCameraViewerState 压成"连接失败"
      setCameraViewerState(win, "error", `${message}（已停止重试，切换图像源或关闭后重开）`);
      showNotice("已自动停止摄像头重试", "warning");
    }
  } finally {
    clearTimeout(timeout);
    win.captureInFlight = false;
    buttons.forEach((button) => { button.disabled = false; });
    renderCameraLiveIndicator(win);
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
  if (hide) {
    revokeCameraObjectUrls(win);
    if (win.imageEl) win.imageEl.removeAttribute("src");
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
    // 遥测 250ms 一刷，顺手刷新状态徽标与帧率徽标：飞控链路和检测开关
    // 都可能在两帧画面之间变化，帧率徽标里的检测速率要跟着一起翻
    renderCameraLiveIndicator(win);
    renderCameraDetection(win);
    renderCameraPerf(win);
    if (!win.vehicleSelect) return;
    const select = win.vehicleSelect;
    // 期望选项 = 遥测机名 + 本窗口配置的机名（不在遥测里时按 custom 保留，
    // 否则重建会把用户选中的项冲掉，外观就是标签在"跳"）。
    const custom = String(win.settings?.vehicle_name || "").trim();
    const desired = names.length ? names.slice() : [""];
    if (custom && !desired.includes(custom)) desired.push(custom);
    const label = (value) => (!value ? "等待 AirSim" : (names.includes(value) ? value : `${value} · custom`));
    const current = [...select.options].map((option) => `${option.value}\u0000${option.textContent}`);
    const wanted = desired.map((value) => `${value}\u0000${label(value)}`);
    // 只有选项真的变了才重建；同时重建后立刻恢复原选中值，绝不让 select 空着
    if (current.length === wanted.length && current.every((item, index) => item === wanted[index])) return;
    const previous = select.value || custom;
    select.textContent = "";
    desired.forEach((value) => select.add(new Option(label(value), value)));
    if (previous && desired.includes(previous)) {
      select.value = previous;
    } else {
      select.value = names[0] || "";
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

