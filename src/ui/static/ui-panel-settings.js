// ui-panel-settings.js —— 设置面板：通信链路、摄像头、模型、技能、审计与任务记录
// 由 app.js 拆分而来；各文件共享同一份脚本作用域，按顺序加载。

function mergeApplicationSettings(raw) {
  const source = raw && typeof raw === "object" ? raw : {};
  return Object.fromEntries(
    Object.entries(DEFAULT_APPLICATION_SETTINGS).map(([group, defaults]) => [
      group,
      { ...defaults, ...(source[group] && typeof source[group] === "object" ? source[group] : {}) },
    ]),
  );
}


async function loadApplicationSettings(force = false) {
  if (applicationSettingsLoaded && !force) return applicationSettings;
  try {
    const data = await api("/api/settings/application");
    applicationSettings = mergeApplicationSettings(data.application);
    applicationSettingsLoaded = true;
    fillApplicationSettingsForm();
    currentLayerKey = applicationSettings.map.default_layer || "satellite";
    if (maplibreMap) applyMapLayer(currentLayerKey);
    restartMainTelemetryRefresh();
  } catch (error) {
    applicationSettings = mergeApplicationSettings(applicationSettings);
    showNotice(`应用设置加载失败: ${error.message || "未知错误"}`, "error");
  }
  return applicationSettings;
}

function fillApplicationSettingsForm() {
  const settings = applicationSettings;
  if (els.appDensity) els.appDensity.value = settings.appearance.density;
  if (els.appMapLayer) els.appMapLayer.value = settings.map.default_layer;
  if (els.appTelemetryRefresh) els.appTelemetryRefresh.value = String(settings.telemetry.refresh_ms);
  if (els.appSetupRefresh) els.appSetupRefresh.value = String(settings.telemetry.setup_refresh_ms);
  if (els.appHistorySeconds) els.appHistorySeconds.value = String(settings.telemetry.history_seconds);
  if (els.appFollowVehicle) els.appFollowVehicle.checked = Boolean(settings.map.follow_vehicle);
  if (els.appShowTrack) els.appShowTrack.checked = Boolean(settings.map.show_vehicle_track);
  if (els.appMissionAltitude) els.appMissionAltitude.value = String(settings.mission.default_altitude_m);
  if (els.appMissionSpeed) els.appMissionSpeed.value = String(settings.mission.default_speed_mps);
  if (els.appMissionHold) els.appMissionHold.value = String(settings.mission.default_hold_s);
  if (els.appMissionAccept) els.appMissionAccept.value = String(settings.mission.default_acceptance_radius_m);
  if (els.appRequireMissionGps) els.appRequireMissionGps.checked = Boolean(settings.safety.require_gps_for_global_mission);
  if (els.appShowContext) els.appShowContext.checked = Boolean(settings.agent.show_context_usage);
  if (els.appAutoMultimodal) els.appAutoMultimodal.checked = Boolean(settings.agent.auto_select_multimodal_model);
  if (els.appMaxMapJump) els.appMaxMapJump.value = String(settings.safety.max_display_jump_m);
  document.body.dataset.density = settings.appearance.density || "comfortable";
}

// 注意：通用/地图/任务默认值/安全 面板已移除，对应输入框不再存在。
// 这里的兜底值必须沿用"已加载的设置"，绝不能用写死的默认值——否则一旦
// 有人调用它，就会把用户已保存的地图/任务默认值悄悄冲掉。
function applicationSettingsFromForm() {
  return mergeApplicationSettings({
    appearance: {
      // 语言/主题无可切换实现，保持已加载值，不写死以免覆盖
      language: applicationSettings.appearance.language,
      theme: applicationSettings.appearance.theme,
      density: els.appDensity?.value || applicationSettings.appearance.density,
    },
    map: {
      default_layer: els.appMapLayer?.value || applicationSettings.map.default_layer,
      follow_vehicle: els.appFollowVehicle ? Boolean(els.appFollowVehicle.checked) : applicationSettings.map.follow_vehicle,
      show_vehicle_track: els.appShowTrack ? Boolean(els.appShowTrack.checked) : applicationSettings.map.show_vehicle_track,
    },
    telemetry: {
      refresh_ms: Number(els.appTelemetryRefresh?.value || applicationSettings.telemetry.refresh_ms),
      setup_refresh_ms: Number(els.appSetupRefresh?.value || applicationSettings.telemetry.setup_refresh_ms),
      history_seconds: Number(els.appHistorySeconds?.value || applicationSettings.telemetry.history_seconds),
      chart_sample_hz: applicationSettings.telemetry.chart_sample_hz,
    },
    mission: {
      default_altitude_m: Number(els.appMissionAltitude?.value || applicationSettings.mission.default_altitude_m),
      default_speed_mps: Number(els.appMissionSpeed?.value || applicationSettings.mission.default_speed_mps),
      default_hold_s: Number(els.appMissionHold?.value || applicationSettings.mission.default_hold_s),
      default_acceptance_radius_m: Number(els.appMissionAccept?.value || applicationSettings.mission.default_acceptance_radius_m),
    },
    safety: {
      // 真实飞控确认由后端能力强制开启，无 UI 开关，保持已加载值
      confirm_real_vehicle_actions: applicationSettings.safety.confirm_real_vehicle_actions,
      require_gps_for_global_mission: els.appRequireMissionGps ? Boolean(els.appRequireMissionGps.checked) : applicationSettings.safety.require_gps_for_global_mission,
      max_display_jump_m: Number(els.appMaxMapJump?.value || applicationSettings.safety.max_display_jump_m),
    },
    agent: {
      show_context_usage: els.appShowContext ? Boolean(els.appShowContext.checked) : applicationSettings.agent.show_context_usage,
      auto_select_multimodal_model: els.appAutoMultimodal ? Boolean(els.appAutoMultimodal.checked) : applicationSettings.agent.auto_select_multimodal_model,
      persist_full_session_history: applicationSettings.agent.persist_full_session_history,
    },
  });
}

async function saveApplicationSettings() {
  const next = applicationSettingsFromForm();
  const data = await post("/api/settings/application", next);
  if (!data.ok) throw new Error(data.error || "保存失败");
  applicationSettings = mergeApplicationSettings(data.application);
  fillApplicationSettingsForm();
  applyMapLayer(applicationSettings.map.default_layer);
  restartVehicleTelemetryPolling();
  restartMainTelemetryRefresh();
  showNotice("应用设置已保存", "info");
}

async function loadConnectionSettings(force = false) {
  if (connectionSettingsLoaded && !force) {
    return {
      auto_connect: autoConnectEnabled,
      active_connection_id: activeConnectionId,
      connections: connectionsCache,
      detected_mavlink_links: detectedMavlinkLinksCache,
      vehicle_info: vehicleInfoCache,
    };
  }
  try {
    const data = await api("/api/settings/connections");
    connectionsCache = Array.isArray(data.connections) ? data.connections : [];
    autoConnectEnabled = Boolean(data.auto_connect);
    activeConnectionId = String(data.active_connection_id || "");
    detectedMavlinkLinksCache = Array.isArray(data.detected_mavlink_links) ? data.detected_mavlink_links : [];
    vehicleInfoCache = data.vehicle_info || vehicleInfoCache;
    if (vehicleInfoCache?.backend && vehicleInfoCache.backend !== "px4_mavlink") {
      vehicleParametersCache = { status: "error", connected: Boolean(vehicleInfoCache.connected), message: "PX4 MAVLink 后端才支持参数读取", parameters: [] };
    } else if (vehicleInfoCache?.connected === false) {
      vehicleParametersCache = { status: "disconnected", connected: false, parameters: [] };
    } else if (vehicleInfoCache?.parameters && !vehicleParametersCache) {
      vehicleParametersCache = vehicleInfoCache.parameters;
    }
    connectionSettingsLoaded = true;
  } catch (error) {
    // 后端不可用时保持内存状态，避免覆盖。
    showNotice("连接设置加载失败: " + (error.message || "未知错误"), "error");
  }
  return {
    auto_connect: autoConnectEnabled,
    active_connection_id: activeConnectionId,
    connections: connectionsCache,
    detected_mavlink_links: detectedMavlinkLinksCache,
    vehicle_info: vehicleInfoCache,
  };
}

async function saveConnectionSettings() {
  try {
    await post("/api/settings/connections", {
      auto_connect: autoConnectEnabled,
      active_connection_id: activeConnectionId,
      connections: connectionsCache,
    });
    return true;
  } catch (error) {
    showNotice("保存连接设置失败: " + (error.message || "未知错误"), "error");
    return false;
  }
}

async function loadVehicleInfo(refresh = false) {
  try {
    const query = refresh ? "?refresh=1" : "";
    const data = await api(`/api/settings/vehicle-info${query}`);
    vehicleInfoCache = data.vehicle_info || data || {};
    if (vehicleInfoCache?.backend && vehicleInfoCache.backend !== "px4_mavlink") {
      vehicleParametersCache = { status: "error", connected: Boolean(vehicleInfoCache.connected), message: "PX4 MAVLink 后端才支持参数读取", parameters: [] };
    } else if (vehicleInfoCache?.connected === false) {
      vehicleParametersCache = { status: "disconnected", connected: false, parameters: [] };
    } else if (vehicleInfoCache?.parameters && !vehicleParametersCache) {
      vehicleParametersCache = vehicleInfoCache.parameters;
    }
    renderVehicleSettingsPanel();
    renderActualLinkCard();
    return vehicleInfoCache;
  } catch (error) {
    vehicleInfoCache = {
      status: "error",
      message: error.message || "vehicle info unavailable",
    };
    renderVehicleSettingsPanel();
    return vehicleInfoCache;
  }
}

async function loadVehicleParameters(refresh = false) {
  const search = String(els.vehicleParameterSearch?.value || "").trim();
  const params = new URLSearchParams({
    limit: "300",
    offset: "0",
    timeout: refresh ? "24" : "18",
  });
  if (refresh) params.set("refresh", "1");
  if (search) params.set("q", search);
  vehicleParametersLoading = true;
  renderVehicleParametersPanel();
  try {
    const data = await api(`/api/settings/vehicle-parameters?${params.toString()}`);
    vehicleParametersCache = data.parameter_info || data || {};
    if (vehicleInfoCache && typeof vehicleInfoCache === "object") {
      vehicleInfoCache.parameters = {
        status: vehicleParametersCache.status,
        ready: vehicleParametersCache.ready,
        received_count: vehicleParametersCache.received_count,
        expected_count: vehicleParametersCache.expected_count,
        missing_count: vehicleParametersCache.missing_count,
        progress: vehicleParametersCache.progress,
        message: vehicleParametersCache.message,
      };
    }
    renderVehicleSettingsPanel();
    renderVehicleParametersPanel();
    return vehicleParametersCache;
  } catch (error) {
    vehicleParametersCache = {
      status: "error",
      connected: false,
      message: error.message || "vehicle parameters unavailable",
      parameters: [],
    };
    renderVehicleParametersPanel();
    return vehicleParametersCache;
  } finally {
    vehicleParametersLoading = false;
    renderVehicleParametersPanel();
  }
}

function isVehicleSetupSection(section = activeSystemSettingsSection) {
  return [
    "vehicle",
    "airframe",
    "sensors",
    "radio",
    "flight_modes",
    "power",
    "actuators",
    "safety",
    "pid_tuning",
    "waveforms",
    "flight_behavior",
    "parameters",
    "firmware",
  ].includes(section);
}

function isRealtimeVehicleSetupSection(section = activeSystemSettingsSection) {
  return [
    "vehicle",
    "sensors",
    "radio",
    "power",
    "actuators",
    "safety",
    "pid_tuning",
    "waveforms",
  ].includes(section);
}

async function loadVehicleSetup(force = false) {
  if (vehicleSetupLoading && !force) return vehicleSetupCache;
  vehicleSetupLoading = true;
  const includeHistory = force || !Object.keys(vehicleSetupCache?.history || {}).length;
  const historyLimit = Math.min(
    2400,
    Math.max(120, Number(applicationSettings.telemetry.history_seconds || 60) * Number(applicationSettings.telemetry.chart_sample_hz || 20)),
  );
  const params = new URLSearchParams({ history: includeHistory ? "1" : "0", limit: String(historyLimit) });
  try {
    const data = await api(`/api/settings/vehicle-setup?${params.toString()}`);
    mergeVehicleSetupSnapshot(data.vehicle_setup || data || {}, { replace: false });
    renderActiveVehicleSetupPanel("setup");
    return vehicleSetupCache;
  } catch (error) {
    mergeVehicleSetupSnapshot({
      status: "error",
      connected: false,
      message: error.message || "vehicle setup unavailable",
      history: {},
    }, { replace: true });
    renderActiveVehicleSetupPanel("setup-error");
    return vehicleSetupCache;
  } finally {
    vehicleSetupLoading = false;
  }
}

function startVehicleSetupPolling() {
  if (!isVehicleSetupSection()) return;
  if (!vehicleSetupPollTimer) {
    loadVehicleSetup(false).catch(() => {});
    vehicleSetupPollTimer = setInterval(() => {
      if (els.systemSettingsModal?.hidden || !isVehicleSetupSection()) {
        stopVehicleSetupPolling();
        return;
      }
      loadVehicleSetup(false).catch(() => {});
    }, VEHICLE_SETUP_POLL_MS);
  }
  restartVehicleTelemetryPolling();
}

function stopVehicleSetupPolling() {
  if (vehicleSetupPollTimer) {
    clearInterval(vehicleSetupPollTimer);
    vehicleSetupPollTimer = null;
  }
  if (vehicleTelemetryPollTimer) {
    clearInterval(vehicleTelemetryPollTimer);
    vehicleTelemetryPollTimer = null;
  }
}

function isConnectionActive(connId) {
  const toolRuntime = latestState?.tool_runtime || {};
  const connected = Boolean(toolRuntime.connected) && !toolRuntime.stale_connection;
  if (!connected) return false;
  // 唯一判据：实际链路识别出来的那条连接（识别不出来就是没有，不再硬指一条）。
  return connId === refreshLiveConnectionId(currentActualLink(), connected);
}

// 0 = 这条连接和当前链路无关
function connectionLinkScore(conn, link = currentActualLink()) {
  const params = conn?.params || {};
  const url = String(link?.url || "");
  if (!url) return 0;
  if (/^https?:\/\//i.test(url)) {
    // ROS2 网关 / AirSim RPC：按完整 URL 或 host:port 比
    const configuredUrl = String(params.url || "").trim().replace(/\/+$/, "");
    if (configuredUrl && configuredUrl === url.replace(/\/+$/, "")) return 8;
    let parsedHost = "";
    let parsedPort = 0;
    try {
      const parsed = new URL(url);
      parsedHost = parsed.hostname;
      parsedPort = Number(parsed.port || (parsed.protocol === "https:" ? 443 : 80));
    } catch (error) {
      return 0;
    }
    const configuredPort = Number(String(params.port ?? params.portNumber ?? "").trim());
    if (configuredPort && configuredPort !== parsedPort) return 0;
    return hostMatches(parsedHost, params.host || params.ip) ? 6 : 0;
  }
  if (url.startsWith("serial:")) {
    const device = String(url.split(":")[1] || "");
    const configured = String(params.port || "").trim();
    if (!configured || normalizeHost(device) !== normalizeHost(configured)) return 0;
    const baud = String(url.split(":")[2] || "").trim();
    return 4 + (baud && String(params.baud || "").trim() === baud ? 2 : 0);
  }
  const urlHost = String(url.split(":")[1] || "");
  const urlPort = Number(String(url.split(":").pop() || "").trim());
  if (url.startsWith("tcp:")) {
    if (!hostMatches(urlHost, params.address)) return 0;
    return 4 + (!params.portNumber || Number(params.portNumber) === urlPort ? 2 : 0);
  }
  const configuredPort = Number(String(params.portNumber || "").trim());
  if (!configuredPort || configuredPort !== urlPort) return 0;
  let score = 2;
  if (url.startsWith("udpout:") || url.startsWith("udp:")) {
    // 直接发往配置的 host:port，地址说了算
    return hostMatches(urlHost, params.host) ? score + 4 : 0;
  }
  // 监听口：host 被 0.0.0.0 取代，用真实心跳来源认领
  const peer = peerEndpointParts(link);
  const configuredHost = String(params.host || "").trim();
  if (peer.host && hostMatches(peer.host, configuredHost)) {
    score += 4;
  } else if (peer.host && isLoopbackHost(configuredHost) && isPrivateHost(peer.host)) {
    // 本机 / WSL 的 SITL 就是走这个监听口的，属于 127.0.0.1 那条
    score += 3;
  } else if (peer.port && Number(params.remotePort) === peer.port) {
    score += 2;
  }
  return score;
}

function refreshLiveConnectionId(link = currentActualLink(), connectedOverride = null) {
  const runtime = latestState?.tool_runtime || {};
  const connected = connectedOverride == null
    ? Boolean(runtime.connected) && !runtime.stale_connection
    : Boolean(connectedOverride);
  if (!connected) {
    liveConnectionCache = { key: "offline", id: "" };
    return "";
  }
  const connections = Array.isArray(connectionsCache) ? connectionsCache : [];
  const key = [
    String(link.url || ""),
    String(link.actual_peer_endpoint || ""),
    String(activeConnectionId || ""),
    connections.length,
  ].join("|");
  if (key === liveConnectionCache.key) return liveConnectionCache.id;
  const ranked = connections
    .map((conn) => {
      let score = connectionLinkScore(conn, link);
      // 同分时优先用户刚点过的那条，避免两条都像的时候来回跳
      if (score > 0 && String(conn.id || "") === String(activeConnectionId || "")) score += 1;
      return { id: String(conn.id || ""), score };
    })
    .filter((entry) => entry.id && entry.score > 0)
    .sort((left, right) => right.score - left.score);
  let identified = ranked.length ? ranked[0].id : "";
  if (!identified) {
    // 认不出来时是否沿用上次激活的连接：链路没有端点信息（AirSim 后端不回报
    // url）才允许，否则必须能被这条连接解释。以前按"列表里第一条同后端的预设"
    // 硬指一条，于是用户新加的 127.0.0.1 链路会被显示成连在老的 JETSON 预设上。
    const persisted = connections.find((conn) => String(conn.id || "") === String(activeConnectionId || ""));
    if (persisted && (!link.url || connectionLinkScore(persisted, link) > 0)) {
      identified = String(persisted.id || "");
    }
  }
  liveConnectionCache = { key, id: identified };
  return identified;
}













async function deleteSelectedConnection() {
  if (!selectedConnectionId) return;
  connectionsCache = connectionsCache.filter((c) => c.id !== selectedConnectionId);
  if (activeConnectionId === selectedConnectionId) {
    activeConnectionId = "";
  }
  const saved = await saveConnectionSettings();
  if (!saved) return;
  selectedConnectionId = connectionsCache[0]?.id || "";
  renderConnectionsList();
  renderConnectionDetail(selectedConnectionId);
}

async function saveAutoConnectEnabled(enabled) {
  autoConnectEnabled = Boolean(enabled);
  await saveConnectionSettings();
}

function setupSystemSettingsResize() {
  const card = els.systemSettingsModal && els.systemSettingsModal.querySelector(".modal-card");
  if (!card) return;
  card.querySelectorAll(".resize-handle").forEach((handle) => {
    handle.addEventListener("mousedown", (e) => startSystemSettingsResize(handle, e, card));
  });
}

function startSystemSettingsResize(handle, e, card) {
  if (card.classList.contains("is-maximized")) return;
  e.preventDefault();
  e.stopPropagation();
  // 脱钩居中变换: 卡片之前用 transform: translate(-50%,-50%) 居中, 拖拽前要先切到显式 px 定位,
  // 否则 transform 仍会作用于盒子, 后续 left/top 改动会被先偏移再定位, 拖拽会跳.
  const rect = card.getBoundingClientRect();
  card.style.transition = "none";
  card.style.transform = "none";
  card.style.left = rect.left + "px";
  card.style.top = rect.top + "px";
  card.style.width = rect.width + "px";
  card.style.height = rect.height + "px";

  const startX = e.clientX;
  const startY = e.clientY;
  const startLeft = rect.left;
  const startTop = rect.top;
  const startWidth = rect.width;
  const startHeight = rect.height;
  const dir = handle.dataset.resize || "";
  const minW = 380;
  const minH = 300;

  function onMove(ev) {
    const dx = ev.clientX - startX;
    const dy = ev.clientY - startY;
    let newLeft = startLeft;
    let newTop = startTop;
    let newWidth = startWidth;
    let newHeight = startHeight;
    if (dir.includes("e")) newWidth = Math.max(minW, startWidth + dx);
    if (dir.includes("w")) {
      newWidth = Math.max(minW, startWidth - dx);
      newLeft = startLeft + (startWidth - newWidth);
    }
    if (dir.includes("s")) newHeight = Math.max(minH, startHeight + dy);
    if (dir.includes("n")) {
      newHeight = Math.max(minH, startHeight - dy);
      newTop = startTop + (startHeight - newHeight);
    }
    card.style.left = newLeft + "px";
    card.style.top = newTop + "px";
    card.style.width = newWidth + "px";
    card.style.height = newHeight + "px";
  }
  function onUp() {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    // 恢复 transition (留空让 CSS 规则重新生效, 不要写回具体值)
    card.style.transition = "";
  }
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

function toggleSystemSettingsMaximize() {
  const card = els.systemSettingsModal && els.systemSettingsModal.querySelector(".modal-card");
  if (!card) return;
  const btn = els.systemSettingsMaximize;
  if (card.classList.contains("is-maximized")) {
    card.classList.remove("is-maximized");
    if (settingsSavedSize) {
      card.style.width = settingsSavedSize.width || "";
      card.style.height = settingsSavedSize.height || "";
      card.style.left = settingsSavedSize.left || "";
      card.style.top = settingsSavedSize.top || "";
      card.style.transform = settingsSavedSize.transform || "";
    }
    btn.innerHTML = SETTINGS_EXPAND_SVG;
    btn.title = "全屏";
  } else {
    settingsSavedSize = {
      width: card.style.width,
      height: card.style.height,
      left: card.style.left,
      top: card.style.top,
      transform: card.style.transform,
    };
    card.classList.add("is-maximized");
    card.style.width = "100vw";
    card.style.height = "100vh";
    card.style.left = "50%";
    card.style.top = "50%";
    card.style.transform = "translate(-50%, -50%)";
    btn.innerHTML = SETTINGS_COLLAPSE_SVG;
    btn.title = "退出全屏";
  }
}

function resetSystemSettingsMaximize() {
  const card = els.systemSettingsModal && els.systemSettingsModal.querySelector(".modal-card");
  if (card) {
    card.classList.remove("is-maximized");
    card.style.width = "";
    card.style.height = "";
    card.style.left = "";
    card.style.top = "";
    card.style.transform = "";
  }
  settingsSavedSize = null;
  if (els.systemSettingsMaximize) {
    els.systemSettingsMaximize.innerHTML = SETTINGS_EXPAND_SVG;
    els.systemSettingsMaximize.title = "全屏";
  }
}

// 每架机的航线颜色（按遥测车辆顺序稳定分配）
function vehicleRouteColor(name) {
  const key = String(name || "");
  const vehicles = Array.isArray(latestState?.tool_runtime?.vehicles) ? latestState.tool_runtime.vehicles : [];
  const idx = vehicles.findIndex((v) => String(v.vehicle_name || "") === key);
  if (idx >= 0) return VEHICLE_ROUTE_PALETTE[idx % VEHICLE_ROUTE_PALETTE.length];
  let hash = 0;
  for (const ch of key) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return VEHICLE_ROUTE_PALETTE[hash % VEHICLE_ROUTE_PALETTE.length];
}

// 多机 marker：无人机图标 + 机名标签（多机模式使用）
function createVehicleElement(name) {
  const el = document.createElement("div");
  el.className = "wp-drone-icon wp-vehicle-marker";
  const label = String(name || "?");
  el.title = `载具 ${label}`;
  el.innerHTML = `
    <svg class="wp-drone-svg" viewBox="0 0 48 48" aria-hidden="true">
      <circle class="wp-drone-ring" cx="24" cy="24" r="17"></circle>
      <path class="wp-drone-body" d="M24 5 L36 39 L24 31 L12 39 Z"></path>
      <circle class="wp-drone-core" cx="24" cy="24" r="4"></circle>
    </svg>
    <span class="wp-vehicle-label">${label}</span>
  `;
  return el;
}

function requireLiveFlightLink() {
  const runtime = activeFlightRuntime();
  if (runtime.connected && !runtime.stale_connection) return runtime;
  const heartbeatAge = Number(runtime.drone?.heartbeat_age_s);
  const age = Number.isFinite(heartbeatAge) ? `，最后心跳 ${heartbeatAge.toFixed(1)}s 前` : "";
  throw new Error(`飞控链路离线或心跳过期${age}，请检查连接设置`);
}

function currentBackendId() {
  const runtime = latestState?.tool_runtime || {};
  return runtime.backend_profile?.id || runtime.backend || "";
}

function isPx4MavlinkBackend() {
  return currentBackendId() === "px4_mavlink";
}



function confirmDialog({ title, message, confirmLabel = "确认", danger = false }) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "waypoint-editor-overlay";
    overlay.innerHTML = `
      <div class="waypoint-editor-card ${danger ? "danger-card" : ""}">
        <header><strong>${escapeHtml(title)}</strong></header>
        <div class="wp-editor-body"><p>${escapeHtml(message)}</p></div>
        <footer>
          <button class="secondary" data-confirm="cancel">取消</button>
          <button class="${danger ? "danger" : "primary"}" data-confirm="ok">${escapeHtml(confirmLabel)}</button>
        </footer>
      </div>
    `;
    document.body.appendChild(overlay);
    const cleanup = (result) => {
      overlay.remove();
      resolve(result);
    };
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) cleanup(false);
    });
    overlay.querySelector('[data-confirm="cancel"]').onclick = () => cleanup(false);
    overlay.querySelector('[data-confirm="ok"]').onclick = () => cleanup(true);
  });
}

async function approveRun(runId) {
  try {
    const resp = await fetch("/api/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: runId }),
    });
    const data = await resp.json();
    if (!data.ok) {
      console.warn("approve failed:", data.error);
    }
  } catch (e) {
    console.error("approve error:", e);
  }
}

async function rejectRun(runId) {
  try {
    const resp = await fetch("/api/reject", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: runId }),
    });
    const data = await resp.json();
    if (!data.ok) {
      console.warn("reject failed:", data.error);
    }
  } catch (e) {
    console.error("reject error:", e);
  }
}

function backendDisplayName(toolRuntime = {}) {
  const profile = toolRuntime.backend_profile || {};
  return profile.name || profile.id || toolRuntime.backend || "Vehicle backend";
}

// 多机列表：HUD 显示每架载具的简要状态（多机模式）
function renderVehicleList(toolRuntime = {}) {
  const container = els.vehicleList;
  if (!container) return;
  const vehicles = Array.isArray(toolRuntime.vehicles) ? toolRuntime.vehicles : [];
  // 相机设置页的车辆名建议（datalist）
  const datalist = document.getElementById("vehicleOptions");
  if (datalist) {
    datalist.textContent = "";
    for (const vehicle of vehicles) {
      const option = document.createElement("option");
      option.value = String(vehicle.vehicle_name || "");
      datalist.appendChild(option);
    }
  }
  // 多机模式：顶部 chips 即目标机选择器（点击切换规划目标，每机一色）
  if (vehicles.length <= 1) {
    // 退化为单机：清掉多机规划状态，航线回到默认单机流程
    if (missionTargetVehicle || Object.keys(missionPlans).length) {
      missionTargetVehicle = "";
      missionPlans = {};
      markMissionEdited();
      renderWaypoints();
      drawMissionPath();
    }
    updateMissionTargetBadge();
    container.hidden = true;
    container.textContent = "";
    syncCameraVehicleOptions(vehicles);
    return;
  }
  container.hidden = false;
  container.textContent = "";
  syncCameraVehicleOptions(vehicles);
  const missionTarget = currentMissionVehicleName();
  for (const vehicle of vehicles) {
    const name = String(vehicle.vehicle_name || "?");
    const state = vehicle.armed ? (vehicle.flying ? "空中" : "待飞") : "未解锁";
    const pos = vehicle.position_ned || {};
    const battery = vehicle.battery_voltage != null ? ` ${fmt(vehicle.battery_voltage)}V` : "";
    const routeColor = vehicleRouteColor(name);
    const planned = (currentMissionVehicleName() === name ? missionWaypoints.length : (missionPlans[name]?.length || 0));
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `hud-vehicle-chip${name === controlSelectionVehicle ? " selected" : ""}`;
    chip.title = `载具 ${name} · ${state}`
      + (planned ? ` · 已画 ${planned} 个航点` : "")
      + `\n点击 = 选中/取消控制目标(可多选);最新选中的为航线规划目标`;
    chip.dataset.vehicle = name;
    chip.innerHTML = `<span class="chip-dot" style="background:${routeColor};box-shadow:0 0 5px ${routeColor}"></span>${escapeHtml(name)}: ${state}${battery ? `<span class="chip-battery">${escapeHtml(battery.trim())}</span>` : ""}${planned ? `<span class="chip-plan-count">${planned}</span>` : ""}`;
    chip.addEventListener("click", () => toggleControlSelection(name));
    container.appendChild(chip);
  }
  updateMissionTargetBadge();
}

function updateVehicleTrack(lngLat, drone, linked, name = "") {
  const source = maplibreMap?.getSource("vehicle-track-source");
  if (!source || !linked || !Array.isArray(lngLat)) return;
  if (!drone?.armed) {
    vehicleTracks.delete(name);
    if (!name) {
      droneTrackActive = false;
      droneTrackLastAzimuth = null;
      droneTrackCoords = [];
    }
    updateVehicleTrackSource();
    return;
  }
  let track = vehicleTracks.get(name) || { coords: [], lastAzimuth: null };
  const last = track.coords[track.coords.length - 1];
  if (last) {
    const distance = haversineMeters(last[1], last[0], lngLat[1], lngLat[0]);
    if (distance < VEHICLE_TRACK_DISTANCE_TOLERANCE_M) return;
    if (distance > 300) {
      track = { coords: [lngLat], lastAzimuth: null };
    }
  }
  if (!track.coords.length) {
    track.coords.push(lngLat);
  } else {
    const prev = track.coords[track.coords.length - 1];
    const azimuth = calculateBearing(prev[1], prev[0], lngLat[1], lngLat[0]);
    const azimuthDelta = track.lastAzimuth == null ? Infinity : angleDeltaDeg(azimuth, track.lastAzimuth);
    if (track.coords.length < 2 || azimuthDelta > VEHICLE_TRACK_AZIMUTH_TOLERANCE_DEG) {
      track.coords.push(lngLat);
      track.lastAzimuth = azimuth;
    } else {
      track.coords[track.coords.length - 1] = lngLat;
    }
  }
  if (track.coords.length > VEHICLE_TRACK_MAX_POINTS) {
    track.coords = track.coords.slice(-VEHICLE_TRACK_MAX_POINTS);
  }
  vehicleTracks.set(name, track);
  if (!name) {
    // 兼容单机旧状态变量（clearVehicleTrack / 其他消费者）
    droneTrackCoords = track.coords;
    droneTrackActive = true;
    droneTrackLastAzimuth = track.lastAzimuth;
  }
  updateVehicleTrackSource();
}

function updateVehicleTrackSource() {
  const source = maplibreMap?.getSource("vehicle-track-source");
  if (!source) return;
  const features = [];
  for (const [name, track] of vehicleTracks.entries()) {
    if (track.coords.length >= 2) {
      features.push({
        type: "Feature",
        properties: { vehicle: name },
        geometry: { type: "LineString", coordinates: track.coords },
      });
    }
  }
  source.setData({ type: "FeatureCollection", features });
}

function clearVehicleTrack() {
  vehicleTracks.clear();
  droneTrackCoords = [];
  droneTrackActive = false;
  droneTrackLastAzimuth = null;
  const source = maplibreMap?.getSource("vehicle-track-source");
  if (source) source.setData({ type: "FeatureCollection", features: [] });
}

function initLayoutPrefs() {
  try {
    const saved = JSON.parse(localStorage.getItem("airsim-agent-layout") || "{}");
    if (saved.left) document.documentElement.style.setProperty("--left-pane", `${saved.left}px`);
    if (saved.right) document.documentElement.style.setProperty("--right-pane", `${saved.right}px`);
    if (saved.timeline) document.documentElement.style.setProperty("--timeline-height", `${saved.timeline}px`);
  } catch (_) {
    localStorage.removeItem("airsim-agent-layout");
  }
}

function saveLayoutPref(key, value) {
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem("airsim-agent-layout") || "{}");
  } catch (_) {
    saved = {};
  }
  saved[key] = value;
  localStorage.setItem("airsim-agent-layout", JSON.stringify(saved));
}

function openAgentSettings() {
  if (els.systemSettingsModal) els.systemSettingsModal.hidden = true;
  if (els.agentSettingsDrawer) els.agentSettingsDrawer.hidden = false;
  if (els.settingsBackdrop) els.settingsBackdrop.hidden = false;
  renderModelConfig();
  renderSkills();
}

function closeAgentSettings() {
  if (els.agentSettingsDrawer) els.agentSettingsDrawer.hidden = true;
  if (els.settingsBackdrop && (!els.systemSettingsModal || els.systemSettingsModal.hidden)) {
    els.settingsBackdrop.hidden = true;
  }
}

function initSystemSettingsDrag() {
  const modal = els.systemSettingsModal;
  const card = modal?.querySelector(".modal-card");
  const header = modal?.querySelector(".modal-head");
  if (!modal || !card || !header) return;

  header.addEventListener("pointerdown", (event) => {
    if (event.target.closest("button, input, select, textarea, a")) return;
    event.preventDefault();
    const startX = event.clientX;
    const startY = event.clientY;
    const startDx = parseFloat(card.dataset.dragX || "0");
    const startDy = parseFloat(card.dataset.dragY || "0");
    document.body.classList.add("dragging-system-settings");
    header.setPointerCapture(event.pointerId);

    const onMove = (moveEvent) => {
      const nx = startDx + (moveEvent.clientX - startX);
      const ny = startDy + (moveEvent.clientY - startY);
      card.style.transform = `translate(calc(-50% + ${nx}px), calc(-50% + ${ny}px))`;
      card.dataset.dragX = String(nx);
      card.dataset.dragY = String(ny);
    };

    const onUp = (upEvent) => {
      header.releasePointerCapture(upEvent.pointerId);
      header.removeEventListener("pointermove", onMove);
      header.removeEventListener("pointerup", onUp);
      document.body.classList.remove("dragging-system-settings");
    };

    header.addEventListener("pointermove", onMove);
    header.addEventListener("pointerup", onUp);
  });
}

async function openSystemSettings() {
  if (els.agentSettingsDrawer) els.agentSettingsDrawer.hidden = true;
  if (els.systemSettingsModal) els.systemSettingsModal.hidden = false;
  if (els.settingsBackdrop) els.settingsBackdrop.hidden = false;
  // 遮罩层显示后, 页面合成层变化可能清空 WebGL 缓冲; 强制地图重绘
  refreshMapAfterLayoutChange();
  const card = els.systemSettingsModal?.querySelector(".modal-card");
  if (card) {
    card.style.transform = "";
    card.dataset.dragX = "0";
    card.dataset.dragY = "0";
  }
  await Promise.all([
    loadApplicationSettings(true),
    loadConnectionSettings(true),
    loadCameraSettings(true),
    loadVehicleInfo(false),
  ]);
  updateVehicleSettingsAvailability();
  selectedConnectionId = activeConnectionId || connectionsCache[0]?.id || "";
  renderConnectionsList();
  renderConnectionDetail(selectedConnectionId);
  setSystemSettingsSection(activeSystemSettingsSection || "links");
  renderSystemConnection();
  loadAirSimSettingsTemplates();
}

async function loadAirSimSettingsTemplates(force = false) {
  if (airsimTemplatesLoaded && !force) return;
  try {
    const data = await api("/api/airsim-settings");
    airsimTemplatesLoaded = true;
    airsimTemplatesCache = data.templates || [];
    renderAirSimSettingsForConnection();
  } catch (error) {
    console.warn("AirSim settings templates load failed:", error);
  }
}

function airsimTemplateForConnectionType(type) {
  // 连接预设 → 模板：AirSim → SimpleFlight; PX4 MAVLink(UDP/TCP/auto/serial) → UDP SITL; ROS2 → TCP 边端
  const mapping = {
    airsim: "airsim_simpleflight_multirotor",
    udp: "px4_mavlink_udp_sitl",
    tcp: "px4_mavlink_udp_sitl",
    auto: "px4_mavlink_udp_sitl",
    serial: "px4_mavlink_udp_sitl",
    px4_ros2: "px4_ros2_tcp_edge",
  };
  return mapping[String(type || "").toLowerCase()] || "";
}

function selectedConnectionTypeForTemplate() {
  // 1) cache 的 connection.type 最可靠 (持久化), 优先
  const detail = connectionsCache.find((c) => c.id === selectedConnectionId);
  if (detail?.type) return detail.type;
  // 2) 新建 (id 为空), 用表单 select 当前值
  if (els.connectionDetailType && els.connectionDetailType.value) {
    return els.connectionDetailType.value;
  }
  return latestState?.tool_runtime?.backend || "";
}

function renderAirSimSettingsForConnection() {
  const wrap = document.getElementById("airsimSettingsTemplates");
  const applyBtn = document.getElementById("airsimTemplateApply");
  const name = document.getElementById("airsimTemplateName");
  const code = document.getElementById("airsimTemplateCode");
  if (!wrap || !applyBtn) return;

  const rawType = selectedConnectionTypeForTemplate();
  const type = String(rawType || "").toLowerCase().trim();
  const matched = airsimTemplateForConnectionType(type);
  const template = airsimTemplatesCache.find((t) => t.id === matched) || null;
  airsimTemplateSelected = template?.id || "";

  // 给应用按钮 dataset 留一份最近一次的 (conn, type, template) 用于兜底/调试;
  // 不再把诊断信息写到可见 DOM 上.
  const connId = selectedConnectionId || "(无)";
  applyBtn.dataset.connId = connId;
  applyBtn.dataset.connectionType = type;
  applyBtn.dataset.templateId = airsimTemplateSelected;
  console.debug("[AirSim template] type=", type, "matched=", matched, "template=", template?.label, "connId=", connId);

  if (!template) {
    wrap.hidden = true;
    applyBtn.hidden = true;
    if (code) code.innerHTML = "";
    return;
  }
  wrap.hidden = false;
    applyBtn.hidden = false;
  if (name) name.textContent = template.label || "—";

  const raw = String(template.content || "");
  const formatted = formatAirSimSettingsJson(raw);
  if (code) {
    code.innerHTML = "";
    code.appendChild(buildHighlightedJsonLines(formatted));
  }
}

// ---- 配置预览美化: 行号 + 语法高亮 ----

function formatAirSimSettingsJson(raw) {
  if (!raw) return "";
  // 模板可能本来就是合法 JSON 字符串, 也可能是带注释或多余空格的近似 JSON.
  // 先尝试解析再 2 空格格式化; 失败则按原文逐行轻处理 (保留行结构, 但去掉空行).
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch (_) {
    return raw.replace(/\r\n/g, "\n").replace(/^\s*\n/gm, "").trimEnd();
  }
}

function buildHighlightedJsonLines(text) {
  const ol = document.createElement("ol");
  ol.className = "airsim-template-lines";
  const lines = (text || "").split("\n");
  for (let i = 0; i < lines.length; i++) {
    const li = document.createElement("li");
    const ln = document.createElement("span");
    ln.className = "ln";
    ln.textContent = String(i + 1);
    const codeSpan = document.createElement("span");
    codeSpan.className = "code";
    const line = lines[i];
    codeSpan.innerHTML = line ? highlightJsonLine(line) : "<span class=\"empty\">·</span>";
    li.append(ln, codeSpan);
    ol.append(li);
  }
  return ol;
}

async function applyAirSimSettingsTemplate() {
  // 应用前再算一次, 同时从按钮 dataset 拉出最近一次 render 算出的 (type, templateId)
  // 任意两者任一非空都作为兜底, 拒绝使用完全 stale 的 airsimTemplateSelected.
  renderAirSimSettingsForConnection();

  const button = document.getElementById("airsimTemplateApply");
  const fallbackType = String(button?.dataset.connectionType || "").toLowerCase();
  const fallbackTemplate = String(button?.dataset.templateId || "");
  if (!airsimTemplateSelected) {
    if (fallbackTemplate && airsimTemplatesCache.some((t) => t.id === fallbackTemplate)) {
      airsimTemplateSelected = fallbackTemplate;
    } else {
      showNotice("当前连接类型没有可用的 AirSim settings 模板", "error");
      return;
    }
  }

  const type = String(selectedConnectionTypeForTemplate() || fallbackType || "").toLowerCase();
  const template = airsimTemplatesCache.find((t) => t.id === airsimTemplateSelected);
  const templateLabel = template?.label || airsimTemplateSelected;
  console.info(
    "[AirSim apply] connId=", button?.dataset.connId,
    "type=", type,
    "template=", airsimTemplateSelected
  );
  if (!confirm(`将备份当前 settings.json 并写入模板：${templateLabel}\n之后需重启 AirSim 生效。继续？`)) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "写入中...";
  try {
    const result = await post("/api/airsim-settings/apply", { template: airsimTemplateSelected });
    const resultEl = document.getElementById("airsimTemplateResult");
    if (resultEl) {
      resultEl.hidden = false;
      resultEl.classList.toggle("error", !result.ok);
      resultEl.textContent = result.ok
        ? `已写入模板「${templateLabel}」到 Documents/AirSim/settings.json${result.backup_path ? "（原文件已备份）" : ""}`
        : `${result.error || "应用失败"}`;
    }
  } catch (error) {
    const resultEl = document.getElementById("airsimTemplateResult");
    if (resultEl) {
      resultEl.hidden = false;
      resultEl.classList.add("error");
      resultEl.textContent = `${error.message || "应用失败"}`;
    }
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function vehicleSettingsAvailable() {
  const runtime = latestState?.tool_runtime || {};
  const backend = String(runtime.backend || vehicleInfoCache?.backend || "");
  return Boolean(runtime.connected && !runtime.stale_connection && backend === "px4_mavlink");
}

function updateVehicleSettingsAvailability() {
  const runtime = latestState?.tool_runtime || {};
  const contract = runtime.operation_contract || {};
  const available = vehicleSettingsAvailable();
  const vehicleKind = String(contract.vehicle_kind || "");
  const source = available
    ? (vehicleKind === "real_px4" ? "REAL USB" : "PX4 SITL")
    : "OFFLINE";
  if (els.vehicleSettingsSource) {
    els.vehicleSettingsSource.textContent = source;
    els.vehicleSettingsSource.classList.toggle("connected", available);
  }
  (els.systemSettingsModal || document).querySelectorAll(".vehicle-only").forEach((button) => {
    button.disabled = !available;
    button.title = available ? `数据来源: ${source}` : "连接 MAVLink PX4 后可用（真实 USB 或 SITL）";
  });
  if (!available && isVehicleSetupSection(activeSystemSettingsSection)) {
    setSystemSettingsSection("links");
  }
}

function closeSystemSettings() {
  if (els.systemSettingsModal) els.systemSettingsModal.hidden = true;
  if (els.settingsBackdrop && (!els.agentSettingsDrawer || els.agentSettingsDrawer.hidden)) {
    els.settingsBackdrop.hidden = true;
  }
  resetSystemSettingsMaximize();
  stopVehicleSetupPolling();
  refreshMapAfterLayoutChange();
}

function setSettingsTab(tab, drawer = document) {
  drawer.querySelectorAll("[data-settings-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.settingsTab === tab);
  });
  drawer.querySelectorAll("[data-settings-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.settingsPanel === tab);
  });
}

function setSystemSettingsSection(section) {
  if (!SYSTEM_SETTINGS_SECTIONS.includes(section)) section = "links";
  activeSystemSettingsSection = section;
  const modal = els.systemSettingsModal || document;
  modal.querySelectorAll("[data-system-section]").forEach((button) => {
    button.classList.toggle("active", button.dataset.systemSection === activeSystemSettingsSection);
  });
  modal.querySelectorAll("[data-system-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.systemPanel === activeSystemSettingsSection);
  });
  stopVehicleSetupPolling();
  if (activeSystemSettingsSection === "links") {
    renderConnectionsList();
    renderConnectionDetail(selectedConnectionId);
  }
}

function findConstraints() {
  const run = latestState && latestState.current_run;
  const steps = run && run.plan ? run.plan.steps : [];
  for (const step of steps || []) {
    if (step.safety && step.safety.constraints) return step.safety.constraints;
  }
  return { max_altitude: 50, max_velocity: 8, geofence_radius: 100 };
}

// 长文本折叠块：默认一行摘要，点击展开完整内容（块级，不挤压同一行）
function collapsibleText(text, className = "") {
  const body = document.createElement("div");
  body.className = `tl-collapse ${className}`.trim();
  const plain = String(text || "");
  if (plain.length <= 90 && !plain.includes("\n")) {
    const p = document.createElement("div");
    p.className = "tl-line";
    p.textContent = plain;
    body.appendChild(p);
    return body;
  }
  const d = document.createElement("details");
  const s = document.createElement("summary");
  s.textContent = plain.replace(/\s+/g, " ").slice(0, 70) + " …（点击展开全文）";
  const pre = document.createElement("pre");
  pre.className = "tl-collapse-full";
  pre.textContent = plain;
  d.appendChild(s);
  d.appendChild(pre);
  body.appendChild(d);
  return body;
}

function compactJson(value, limit = 90) {
  try {
    const text = JSON.stringify(value, (k, v) => (k === "image_base64" ? "<image>" : v));
    return text.length > limit ? text.slice(0, limit) + "…" : text;
  } catch (e) {
    return String(value).slice(0, limit);
  }
}

function normalizeAgentSettingsCopy() {
  const drawer = els.agentSettingsDrawer;
  if (!drawer) return;
  const title = drawer.querySelector(".settings-head strong");
  if (title) title.textContent = "Agent 设置";
  const close = drawer.querySelector("#agentSettingsClose");
  if (close) {
    close.textContent = "×";
    close.title = "关闭";
  }
  const tabCopy = {
    plan: ["任务运行", "当前任务进度与历史复盘"],
    events: ["审计日志", "路由、工具、审批与错误记录"],
    tools: ["工具", "按能力域分类的可调用操作"],
    memory: ["记忆", "上下文、运行态与长期任务经验"],
    llm: ["模型", "LLM 与视觉模型配置"],
    skills: ["技能", "可导入和编辑的无人机操作规程"],
  };
  Object.entries(tabCopy).forEach(([key, copy]) => {
    const button = drawer.querySelector(`[data-settings-tab="${key}"]`);
    if (!button) return;
    button.title = copy[1];
    const span = button.querySelector("span");
    if (span) span.textContent = copy[0];
  });
  const panelCopy = {
    plan: [
      "任务运行",
      "展示当前 Execute 的计划、风险、进度与校验结果；最近任务记录可用于复盘，跨会话经验进入记忆。",
    ],
    events: [
      "审计日志",
      "记录本次服务运行中的模式路由、工具调用、人工审批、结果校验与错误，便于定位真实飞行和仿真问题。",
    ],
    tools: [
      "工具目录",
      "工具按链路、遥测、飞控、导航、任务、感知与安全分类；实际可用性由当前 AirSim、MAVLink 或 ROS2 后端决定。",
    ],
    memory: [
      "记忆管理",
      "完整会话持久化保存；模型上下文按所选模型 token 窗口动态装载。运行态、任务经验、风险与 Skill 候选分开管理。",
    ],
    llm: [
      "模型配置",
      "支持 OpenAI-compatible 与 Anthropic API。输入能力和上下文窗口保存时从厂商模型目录自动识别，离线时按模型 ID 推断，也可手动覆盖。",
    ],
    skills: [
      "技能库",
      "Skill 是面向无人机任务的操作规程，可新建、导入、编辑和停用；执行时只允许使用当前后端具备的工具。",
    ],
  };
  Object.entries(panelCopy).forEach(([key, copy]) => {
    const panel = drawer.querySelector(`[data-settings-panel="${key}"]`);
    if (!panel) return;
    const strong = panel.querySelector(".settings-card > header strong");
    const help = panel.querySelector(".settings-help");
    if (strong) strong.textContent = copy[0];
    if (help) help.textContent = copy[1];
  });
}

function compactJson(value, maxLength = 180) {
  let text = "";
  try {
    text = JSON.stringify(value);
  } catch (_) {
    text = String(value || "");
  }
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
}

function normalizeSystemSettingsCopy() {
  const drawer = document.getElementById("systemSettingsModal");
  if (!drawer) return;
  const close = document.getElementById("systemSettingsClose");
  if (close) {
    close.textContent = "×";
    close.title = "关闭";
  }
  const add = document.getElementById("addConnectionBtn");
  if (add) add.textContent = "+ 添加连接";
  const setLabel = (id, text) => {
    const label = drawer.querySelector(`label[for="${id}"]`);
    if (label) label.textContent = text;
  };
  setLabel("connectionDetailName", "名称");
  setLabel("connectionDetailType", "类型");
  setLabel("connectionDetailPort", "串口");
  setLabel("connectionDetailHost", "PX4 主机");
  setLabel("connectionDetailAddress", "TCP 地址");
  setLabel("connectionDetailPortNumber", "端口 / 波特率");
  setLabel("connectionDetailRemotePort", "PX4 远端端口");
  setLabel("connectionDetailRealVehicle", "真实飞控");
  if (els.connectionDetailName) els.connectionDetailName.placeholder = "PX4 MAVLink";
  if (els.connectionDetailPort) els.connectionDetailPort.placeholder = "COM3 or /dev/ttyACM0";
  if (els.connectionDetailAddress) els.connectionDetailAddress.placeholder = "127.0.0.1";
  if (els.connectionDetailRemotePort) els.connectionDetailRemotePort.placeholder = "18570 (optional)";
  if (els.connectionDetailCancel) els.connectionDetailCancel.textContent = "取消";
  const submit = els.connectionDetailForm?.querySelector('button[type="submit"]');
  if (submit) submit.textContent = "保存设置";
  const options = {
    auto: "自动",
    serial: "Serial",
    udp: "UDP MAVLink",
    tcp: "TCP MAVLink",
    airsim: "AirSim",
    px4_ros2: "PX4 ROS2 Gateway",
  };
  Array.from(els.connectionDetailType?.options || []).forEach((option) => {
    option.textContent = options[option.value] || option.value;
  });
}

function connectionTypeLabel(type) {
  return {
    auto: "Auto MAVLink",
    serial: "Serial",
    udp: "UDP MAVLink",
    tcp: "TCP MAVLink",
    airsim: "AirSim",
    px4_ros2: "PX4 ROS2 Gateway",
    ros2: "PX4 ROS2 Gateway",
    ros: "PX4 ROS2 Gateway",
    px4_ros: "PX4 ROS2 Gateway",
  }[String(type || "").toLowerCase()] || String(type || "unknown");
}

function connectionParamsSummary(params = {}, type = "") {
  const normalizedType = String(type || "").toLowerCase();
  if (normalizedType === "auto") {
    const fallbackHost = params.host || "127.0.0.1";
    const fallbackPort = params.portNumber || "14550";
    return `USB auto first / fallback udp:${fallbackHost}:${fallbackPort}`;
  }
  if (normalizedType === "serial") {
    return `${params.port || "auto port"} / baud: ${params.baud || params.portNumber || "115200"}`;
  }
  const preferred = ["url", "host", "address", "port", "portNumber", "baud", "remotePort", "workspace"];
  const rows = [];
  preferred.forEach((key) => {
    const value = params[key];
    if (value === undefined || value === null || value === "") return;
    rows.push(`${key}: ${value}`);
  });
  return rows.join(" / ") || "default params";
}

function backendLabelFromId(backend) {
  return {
    airsim: "AirSim",
    px4_mavlink: "PX4 MAVLink",
    px4_ros2: "PX4 ROS2 Gateway",
  }[String(backend || "").toLowerCase()] || String(backend || "backend");
}

function currentVehicleInfo() {
  const drone = latestState?.tool_runtime?.drone || {};
  const info = vehicleInfoCache && typeof vehicleInfoCache === "object" ? vehicleInfoCache : {};
  const connection = info.connection || drone.active_link || {};
  const firmware = info.firmware || drone.firmware || {};
  return { ...info, connection, firmware };
}

function currentActualLink() {
  return currentVehicleInfo().connection || {};
}

function currentFirmwareInfo() {
  return currentVehicleInfo().firmware || {};
}

function actualLinkSummary(link = currentActualLink()) {
  const url = link.url || "";
  const detected = link.detected_link || {};
  if (url.startsWith("serial:")) {
    const board = detected.board_name || detected.board_type || "PX4 USB";
    const device = detected.device || url.split(":")[1] || "";
    const baud = detected.baud || url.split(":")[2] || "";
    return `${device} / ${baud} / ${board}`;
  }
  if (url) return url;
  return "未建立实际链路";
}

function mavlinkRemoteTargetSummary(link = currentActualLink()) {
  if (link.px4_remote_endpoint) return String(link.px4_remote_endpoint);
  if (link.px4_remote_host && link.px4_remote_port) {
    return `${link.px4_remote_host}:${link.px4_remote_port}`;
  }
  const target = Array.isArray(link.probe_targets) ? link.probe_targets[0] : null;
  if (target?.host && target?.port) return `${target.host}:${target.port}`;
  return "";
}

function firmwareVersionText(firmware = currentFirmwareInfo()) {
  const version = firmware.flight_version || {};
  if (version.text) return version.type_name ? `${version.text} ${version.type_name}` : version.text;
  const custom = firmware.px4_custom_version || {};
  if (custom.text) return custom.text;
  return "--";
}

function isRealVehicleRuntime(runtime = latestState?.tool_runtime || {}) {
  const capabilities = runtime.backend_profile?.capabilities || {};
  const drone = runtime.drone || {};
  return Boolean(capabilities.real_vehicle || drone.real_vehicle);
}

function renderActualLinkCard() {
  const card = els.connectionActualLink;
  if (!card) return;
  const connected = Boolean(latestState?.tool_runtime?.connected) && !latestState?.tool_runtime?.stale_connection;
  const selectedIsActive = selectedConnectionId && selectedConnectionId === activeConnectionId;
  if (!connected || !selectedIsActive) {
    card.hidden = true;
    card.innerHTML = "";
    return;
  }
  const link = currentActualLink();
  const detected = link.detected_link || {};
  const rows = [];
  const remoteTarget = mavlinkRemoteTargetSummary(link);
  if (String(link.url || "").startsWith("udpin:")) {
    rows.push(["本地监听", link.local_listen_url || link.url]);
    rows.push(["实际心跳来源", actualHeartbeatSourceText(link)]);
    if (remoteTarget) rows.push(["PX4 目标（配置）", remoteTarget]);
  } else {
    rows.push(["实际端点", actualLinkSummary(link)]);
    if (link.actual_peer_endpoint) rows.push(["实际心跳来源", actualHeartbeatSourceText(link)]);
    if (remoteTarget) rows.push(["PX4 目标（配置）", remoteTarget]);
  }
  rows.push(
    ["链路类型", link.real_vehicle ? "真实 USB 飞控" : "仿真/网络链路"],
    ["系统/组件", `${valueText(link.system_id)} / ${valueText(link.component_id)}`],
    ["心跳", link.heartbeat_age_s != null ? `${link.heartbeat_age_s}s` : "--"],
  );
  if (detected.vid != null || detected.pid != null) {
    rows.push(["VID/PID", `${valueText(detected.vid)} / ${valueText(detected.pid)}`]);
  }
  card.innerHTML = `
    <strong>当前实际连接</strong>
    <div class="vehicle-info-grid">
      ${rows.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(String(value))}</dd>`).join("")}
    </div>
  `;
  card.hidden = false;
}

function renderDetectedMavlinkLinks() {
  const list = els.detectedMavlinkLinks;
  if (!list) return;
  const rawLinks = Array.isArray(detectedMavlinkLinksCache) ? detectedMavlinkLinksCache : [];
  const byDevice = new Map();
  rawLinks.forEach((item) => {
    const key = item.device || item.url || `link_${byDevice.size}`;
    const existing = byDevice.get(key);
    if (!existing) {
      byDevice.set(key, { ...item, baud_candidates: [item.baud].filter(Boolean) });
    } else {
      existing.baud_candidates = Array.from(new Set([...(existing.baud_candidates || []), item.baud].filter(Boolean)));
      if (Number(item.score || 0) > Number(existing.score || 0)) Object.assign(existing, item);
    }
  });
  const links = Array.from(byDevice.values());
  const activeUrl = currentActualLink().url || "";
  if (els.detectedMavlinkLinksCount) els.detectedMavlinkLinksCount.textContent = String(links.length);
  if (!links.length) {
    list.innerHTML = `<div class="empty small">未检测到 USB MAVLink 串口。</div>`;
    return;
  }
  list.innerHTML = links.map((item) => {
    const connected = item.url === activeUrl;
    return `
      <article class="detected-link-item ${connected ? "connected" : ""}">
        <strong>${escapeHtml(item.device || "Serial")} ${connected ? "· 已连接" : ""}</strong>
        <span>${escapeHtml(item.board_name || item.board_type || item.description || "MAVLink device")}</span>
        <div class="detected-link-meta">
          <code>${escapeHtml(item.url || "")}</code>
          <code>baud ${escapeHtml((item.baud_candidates || [item.baud]).filter(Boolean).join(", "))}</code>
          <code>VID ${escapeHtml(valueText(item.vid))}</code>
          <code>PID ${escapeHtml(valueText(item.pid))}</code>
          <code>score ${escapeHtml(valueText(item.score))}</code>
        </div>
      </article>
    `;
  }).join("");
}

function renderVehicleSettingsPanel() {
  const panel = els.vehicleInfoPanel;
  if (!panel) return;
  const runtime = latestState?.tool_runtime || {};
  const drone = runtime.drone || {};
  const setup = setupSnapshot();
  const info = currentVehicleInfo();
  const link = setup.connection || info.connection || {};
  const firmware = setup.firmware || info.firmware || {};
  const parameterStatus = setup.parameters || info.parameters || drone.parameter_status || vehicleParametersCache || {};
  const summary = setup.summary || {};
  const mapReliable = hasReliableVehicleMapPosition(setup.telemetry?.status || drone, runtime);
  const detected = link.detected_link || {};
  const connected = setupConnected();
  panel.innerHTML = `
    <div class="vehicle-config-banner ${connected ? "online" : "offline"}">
      <div>
        <strong>${connected ? "PX4 已连接" : "等待 PX4 连接"}</strong>
        <span>${escapeHtml(actualLinkSummary(link))}</span>
      </div>
      <div class="vehicle-config-badges">
        ${setupBadge(connected ? "ready" : "disconnected", connected ? "ONLINE" : "OFFLINE")}
        ${setupBadge(parameterStatus.ready ? "ready" : (parameterStatus.received_count ? "partial" : "warning"), `参数 ${parameterCountText(parameterStatus)}`)}
        ${setupBadge(mapReliable ? "ready" : "warning", mapReliable ? "地图位置可信" : "地图位置未采用")}
      </div>
    </div>
    <div class="setup-summary-flow">
      ${summaryCard("airframe", "机架", summary.airframe, [
        ["系统 ID", summary.airframe?.system_id || link.system_id],
        ["机型", summary.airframe?.vehicle_type || firmware.vehicle_type],
        ["固件版本", firmwareVersionText(firmware)],
        ["板卡", detected.board_name || detected.board_type],
      ])}
      ${summaryCard("sensors", "传感器", summary.sensors, [
        ["陀螺仪", setupStatusLabel(summary.sensors?.gyro)],
        ["加速度计", setupStatusLabel(summary.sensors?.accel)],
        ["磁罗盘", setupStatusLabel(summary.sensors?.mag)],
        ["气压计", setupStatusLabel(summary.sensors?.baro)],
      ])}
      ${summaryCard("radio", "遥控器", summary.radio, [
        ["通道数", summary.radio?.channels || "--"],
        ["RSSI", valueText(summary.radio?.rssi)],
        ["状态", setupStatusLabel(summary.radio?.sensor_state)],
      ])}
      ${summaryCard("flight_modes", "飞行模式", summary.flight_modes, [
        ["当前模式", summary.flight_modes?.current_mode || drone.mode],
        ["模式 1", summary.flight_modes?.flight_mode_1],
        ["模式 2", summary.flight_modes?.flight_mode_2],
        ["模式 3", summary.flight_modes?.flight_mode_3],
      ])}
      ${summaryCard("power", "电源", summary.power, [
        ["电压", formatNumber(summary.power?.voltage, 2, " V")],
        ["电流", formatNumber(summary.power?.current, 2, " A")],
        ["电量", summary.power?.remaining != null ? `${summary.power.remaining}%` : "--"],
        ["电芯", summary.power?.cells || "--"],
      ])}
      ${summaryCard("safety", "安全", summary.safety, [
        ["解锁", summary.safety?.armed ? "已解锁" : "未解锁"],
        ["飞行", summary.safety?.flying ? "空中" : "地面/未知"],
        ["低电量动作", summary.safety?.low_battery_action],
        ["遥控失联", summary.safety?.rc_loss_action],
      ])}
      ${summaryCard("actuators", "Actuators", summary.actuators, [
        ["输出数量", summary.actuators?.outputs || "--"],
        ["活跃输出", summary.actuators?.active_outputs || "--"],
        ["状态", setupStatusLabel(summary.actuators?.sensor_state)],
      ])}
      ${summaryCard("parameters", "参数", { setup: parameterStatus.ready ? "ok" : (parameterStatus.received_count ? "warning" : "missing") }, [
        ["下载状态", parameterStatusText(parameterStatus.status)],
        ["收到/总数", parameterCountText(parameterStatus)],
        ["进度", parameterProgressText(parameterStatus)],
        ["最近收到", timestampText(parameterStatus.last_message_at)],
      ])}
    </div>
    ${readOnlyRibbon()}
  `;
  renderDetectedMavlinkLinks();
}

function renderVehicleSetupPanels(forceAll = false) {
  if (!forceAll) {
    renderActiveVehicleSetupPanel("active");
    return;
  }
  renderVehicleSettingsPanel();
  renderVehicleAirframePanel();
  renderVehicleSensorsPanel(true);
  renderVehicleRadioPanel();
  renderVehicleFlightModesPanel();
  renderVehiclePowerPanel();
  renderVehicleActuatorsPanel();
  renderVehicleSafetyPanel();
  renderVehiclePidPanel(true);
  renderVehicleWaveformPanel(true);
  renderVehicleFlightBehaviorPanel();
  renderVehicleFirmwarePanel();
  renderVehicleParameterSummary(vehicleParametersCache || setupSnapshot().parameters || currentVehicleInfo().parameters || {});
}

function renderActiveVehicleSetupPanel(reason = "") {
  if (els.systemSettingsModal?.hidden || !isVehicleSetupSection(activeSystemSettingsSection)) return;
  if (activeSystemSettingsSection !== "pid_tuning") {
    const pidPanel = els.vehiclePidPanel;
    if (pidPanel) pidPanel.dataset.pidMounted = "";
  }
  switch (activeSystemSettingsSection) {
    case "vehicle":
      renderVehicleSettingsPanel();
      break;
    case "airframe":
      renderVehicleAirframePanel();
      break;
    case "sensors":
      renderVehicleSensorsPanel(reason === "section" || reason === "setup" || reason === "setup-error");
      break;
    case "radio":
      renderVehicleRadioPanel();
      break;
    case "flight_modes":
      renderVehicleFlightModesPanel();
      break;
    case "power":
      renderVehiclePowerPanel();
      break;
    case "actuators":
      renderVehicleActuatorsPanel();
      break;
    case "safety":
      renderVehicleSafetyPanel();
      break;
    case "pid_tuning":
      renderVehiclePidPanel(reason === "section");
      break;
    case "waveforms":
      renderVehicleWaveformPanel(reason === "section");
      break;
    case "flight_behavior":
      renderVehicleFlightBehaviorPanel();
      break;
    case "parameters":
      renderVehicleParameterSummary(vehicleParametersCache || setupSnapshot().parameters || currentVehicleInfo().parameters || {});
      break;
    case "firmware":
      renderVehicleFirmwarePanel();
      break;
    default:
      break;
  }
}

function renderVehicleRadioPanel() {
  const panel = els.vehicleRadioPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const setup = setupSnapshot();
  const radio = setup.summary?.radio || {};
  const channels = setup.telemetry?.rc_channels?.channels || [];
  panel.innerHTML = `
    <div class="setup-two-col">
      <section class="setup-detail-card">
        <strong>遥控器输入</strong>
        ${setupRows([
          ["状态", setupStatusLabel(radio.sensor_state)],
          ["通道数", radio.channels],
          ["RSSI", radio.rssi],
          ["time_boot_ms", radio.last],
        ])}
      </section>
      <section class="setup-detail-card channel-list">
        ${channels.slice(0, 12).map((value, index) => channelBar(`CH${index + 1}`, value)).join("") || `<div class="setup-empty small">未收到 RC_CHANNELS。</div>`}
      </section>
    </div>
    ${dataSourceRibbon(["RC_CHANNELS", "SYS_STATUS"])}
  `;
}

function renderVehiclePowerPanel() {
  const panel = els.vehiclePowerPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const setup = setupSnapshot();
  const power = setup.summary?.power || {};
  const battery = setup.telemetry?.battery || {};
  const remaining = Number(power.remaining ?? battery.battery_remaining);
  const pct = Number.isFinite(remaining) ? Math.max(0, Math.min(100, remaining)) : 0;
  panel.innerHTML = `
    <div class="setup-two-col">
      <section class="setup-detail-card power-gauge-card">
        <div class="battery-gauge"><i style="height:${pct}%"></i></div>
        ${setupRows([
          ["电压", formatNumber(power.voltage, 2, " V")],
          ["电流", formatNumber(power.current, 2, " A")],
          ["剩余", Number.isFinite(remaining) ? `${remaining}%` : "--"],
          ["电芯", power.cells],
          ["状态", setupStatusLabel(power.sensor_state)],
        ])}
      </section>
      <section class="setup-detail-card">
        <strong>电芯电压</strong>
        <div class="axis-grid">
          ${(battery.cell_voltages || []).map((value, index) => axisMeter(`Cell ${index + 1}`, value, 3.0, 4.4, " V", 2)).join("") || `<div class="setup-empty small">未收到 BATTERY_STATUS 电芯数据。</div>`}
        </div>
      </section>
    </div>
    ${dataSourceRibbon(["SYS_STATUS", "BATTERY_STATUS", "POWER_STATUS"])}
  `;
}

function renderVehicleActuatorsPanel() {
  const panel = els.vehicleActuatorsPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const setup = setupSnapshot();
  const actuators = setup.summary?.actuators || {};
  const outputs = setup.telemetry?.servo_output?.outputs || [];
  panel.innerHTML = `
    <div class="setup-two-col">
      <section class="setup-detail-card">
        <strong>执行器状态</strong>
        ${setupRows([
          ["状态", setupStatusLabel(actuators.sensor_state)],
          ["输出数量", actuators.outputs],
          ["活跃输出", actuators.active_outputs],
        ])}
      </section>
      <section class="setup-detail-card channel-list">
        ${outputs.slice(0, 12).map((value, index) => channelBar(`PWM${index + 1}`, value, 900, 2100)).join("") || `<div class="setup-empty small">未收到 SERVO_OUTPUT_RAW。</div>`}
      </section>
    </div>
    ${dataSourceRibbon(["SERVO_OUTPUT_RAW", "SYS_STATUS"])}
    ${readOnlyRibbon()}
  `;
}

function renderVehicleSafetyPanel() {
  const panel = els.vehicleSafetyPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const safety = setupSnapshot().summary?.safety || {};
  panel.innerHTML = `
    <section class="setup-detail-card wide">
      <div class="safety-state-row">
        ${setupBadge(safety.armed ? "warning" : "ready", safety.armed ? "已解锁" : "未解锁")}
        ${setupBadge(safety.flying ? "warning" : "ready", safety.flying ? "空中飞行" : "地面/未知")}
        ${setupBadge("ready", safety.mode || "--")}
      </div>
      ${setupRows([
        ["低电量保护", safety.low_battery_action],
        ["遥控信号丢失保护", safety.rc_loss_action],
        ["数据链路丢失保护", safety.data_link_loss],
        ["返航高度", safety.return_altitude],
      ])}
    </section>
    ${dataSourceRibbon(["HEARTBEAT", "EXTENDED_SYS_STATE", "COM_* / NAV_*"])}
    ${readOnlyRibbon()}
  `;
}

function parameterValueFromCache(name) {
  const params = vehicleParametersCache?.parameters || [];
  const found = params.find((param) => param.name === name);
  if (found) return valueText(found.value_text ?? found.value);
  const setup = setupSnapshot();
  return valueText((setup.parameter_highlights || {})[name], "--");
}

function renderVehicleFlightBehaviorPanel() {
  const panel = els.vehicleFlightBehaviorPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const rows = [
    ["MPC_XY_VEL_MAX", parameterValueFromCache("MPC_XY_VEL_MAX")],
    ["MPC_Z_VEL_MAX_UP", parameterValueFromCache("MPC_Z_VEL_MAX_UP")],
    ["MPC_Z_VEL_MAX_DN", parameterValueFromCache("MPC_Z_VEL_MAX_DN")],
    ["MPC_TKO_SPEED", parameterValueFromCache("MPC_TKO_SPEED")],
    ["NAV_ACC_RAD", parameterValueFromCache("NAV_ACC_RAD")],
    ["COM_RC_LOSS_T", parameterValueFromCache("COM_RC_LOSS_T")],
  ];
  panel.innerHTML = `<section class="setup-detail-card wide">${setupRows(rows)}</section>${dataSourceRibbon(["MPC_*", "NAV_*", "COM_*"])}${readOnlyRibbon()}`;
}

function renderVehicleFirmwarePanel() {
  const panel = els.vehicleFirmwarePanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const setup = setupSnapshot();
  const firmware = setup.firmware || currentFirmwareInfo();
  const capabilities = Array.isArray(firmware.capability_flags) ? firmware.capability_flags : [];
  panel.innerHTML = `
    <section class="setup-detail-card wide">
      ${setupRows([
        ["PX4 版本", firmwareVersionText(firmware)],
        ["Autopilot", firmware.autopilot],
        ["机型", firmware.vehicle_type],
        ["Vendor/Product", `${valueText(firmware.vendor_id)} / ${valueText(firmware.product_id)}`],
        ["UID", firmware.uid],
        ["Git hash", firmware.git_hash],
        ["Flight custom", firmware.flight_custom_version_hex],
      ])}
      <div class="capability-list">
        ${capabilities.map((item) => `<span>${escapeHtml(item)}</span>`).join("") || `<span>未收到能力标志</span>`}
      </div>
    </section>
    ${dataSourceRibbon(["AUTOPILOT_VERSION", "HEARTBEAT"])}
    ${readOnlyRibbon()}
  `;
}

function parameterStatusText(status) {
  return {
    disconnected: "未连接",
    not_requested: "未请求",
    idle: "未请求",
    receiving: "正在接收",
    downloading: "正在读取",
    ready: "已完成",
    partial: "部分收到",
    busy: "运行时忙",
    error: "读取失败",
  }[String(status || "").toLowerCase()] || valueText(status);
}

function parameterCountText(info = {}) {
  const received = Number(info.received_count || 0);
  const expected = Number(info.expected_count || 0);
  if (expected > 0) return `${received} / ${expected}`;
  return received ? `${received}` : "--";
}

function parameterProgressText(info = {}) {
  const progress = Number(info.progress);
  if (Number.isFinite(progress)) return `${Math.round(progress * 100)}%`;
  return "--";
}

function renderVehicleParameterSummary(info = vehicleParametersCache || currentVehicleInfo().parameters || {}) {
  if (!els.vehicleParameterSummary) return;
  const runtime = latestState?.tool_runtime || {};
  const connected = setupConnected() || (Boolean(runtime.connected) && !runtime.stale_connection);
  const statusClass = connected && info.status === "ready" ? "ready" : (connected ? "pending" : "offline");
  els.vehicleParameterSummary.innerHTML = `
    <div class="parameter-stat ${statusClass}">
      <span>状态</span>
      <strong>${escapeHtml(parameterStatusText(info.status || (connected ? "not_requested" : "disconnected")))}</strong>
    </div>
    <div class="parameter-stat">
      <span>收到/总数</span>
      <strong>${escapeHtml(parameterCountText(info))}</strong>
    </div>
    <div class="parameter-stat">
      <span>进度</span>
      <strong>${escapeHtml(parameterProgressText(info))}</strong>
    </div>
    <div class="parameter-stat">
      <span>最近收到</span>
      <strong>${escapeHtml(timestampText(info.last_message_at))}</strong>
    </div>
  `;
}

function renderParameterGroupChips() {
  const groups = setupSnapshot().parameter_groups || {};
  const entries = Object.entries(groups).slice(0, 18);
  if (!entries.length) return "";
  return `
    <div class="parameter-groups">
      <button type="button" data-param-group="">全部</button>
      ${entries.map(([name, count]) => `<button type="button" data-param-group="${escapeHtml(name)}">${escapeHtml(name)} <span>${escapeHtml(valueText(count))}</span></button>`).join("")}
    </div>
  `;
}

async function saveVehicleParameterFromRow(button) {
  const row = button.closest("[data-param-name]");
  if (!row) return;
  const input = row.querySelector("[data-param-input]");
  const name = row.dataset.paramName || "";
  const value = input ? input.value : "";
  const componentId = row.dataset.paramComponent || "";
  const paramType = row.dataset.paramType || "";
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "写入中";
  try {
    const result = await post("/api/settings/vehicle-parameters/set", {
      name,
      value,
      component_id: componentId,
      param_type: paramType,
      timeout: 3.5,
    });
    const payload = result.parameter_write || result;
    if (!result.ok || payload.status !== "ok") {
      throw new Error(payload.message || result.error || "parameter write failed");
    }
    const updated = payload.parameter || {};
    if (vehicleParametersCache?.parameters && updated.name) {
      const idx = vehicleParametersCache.parameters.findIndex((param) =>
        param.name === updated.name && String(param.component_id || "") === String(updated.component_id || "")
      );
      if (idx >= 0) vehicleParametersCache.parameters[idx] = { ...vehicleParametersCache.parameters[idx], ...updated };
    }
    showNotice(`${name} 已写入并收到飞控确认`, "success");
    await loadVehicleParameters(false);
    await loadVehicleSetup(true);
  } catch (error) {
    showNotice(`参数写入失败: ${error.message || "未知错误"}`, "error");
  } finally {
    button.disabled = false;
    button.textContent = oldText || "保存";
  }
}

function renderVehicleParametersPanel() {
  const panel = els.vehicleParametersPanel;
  if (!panel) return;
  const runtime = latestState?.tool_runtime || {};
  const connected = setupConnected() || (Boolean(runtime.connected) && !runtime.stale_connection);
  const info = vehicleParametersCache || currentVehicleInfo().parameters || {};
  const parameters = Array.isArray(info.parameters) ? info.parameters : [];
  renderVehicleParameterSummary(info);

  if (vehicleParametersLoading && !parameters.length) {
    panel.innerHTML = `${renderParameterGroupChips()}<div class="parameter-empty">正在读取 PX4 参数...</div>`;
    return;
  }
  if (!connected) {
    panel.innerHTML = `${renderParameterGroupChips()}<div class="parameter-empty">未连接 PX4，连接后可读取参数。</div>`;
    return;
  }
  if (!parameters.length) {
    const status = parameterStatusText(info.status || "not_requested");
    const message = info.message ? ` · ${info.message}` : "";
    panel.innerHTML = `${renderParameterGroupChips()}<div class="parameter-empty">${escapeHtml(status + message)}</div>`;
    return;
  }

  panel.innerHTML = `
    ${renderParameterGroupChips()}
    <div class="parameter-edit-hint">逐项保存会发送 MAVLink PARAM_SET，并等待飞控回传 PARAM_VALUE 确认；不会批量写入，也不会自动重启飞控。</div>
    <div class="parameter-table-wrap">
      <table class="parameter-table">
        <thead>
          <tr>
            <th>名称</th>
            <th>值 / 写入</th>
            <th>类型</th>
            <th>组件</th>
            <th>Index</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${parameters.map((param) => `
            <tr data-param-name="${escapeHtml(param.name || "")}" data-param-component="${escapeHtml(valueText(param.component_id, ""))}" data-param-type="${escapeHtml(valueText(param.type, ""))}">
              <td><code>${escapeHtml(param.name || "")}</code></td>
              <td>
                <input class="parameter-value-input" data-param-input type="text" value="${escapeHtml(valueText(param.value_text ?? param.value, ""))}" spellcheck="false">
              </td>
              <td>${escapeHtml(valueText(param.type_name || param.type))}</td>
              <td>${escapeHtml(valueText(param.component_id))}</td>
              <td>${escapeHtml(valueText(param.index))}</td>
              <td><button type="button" class="parameter-save-btn" data-param-save>保存</button></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
    <div class="parameter-footer">
      <span>${escapeHtml(`显示 ${parameters.length} / ${Number(info.total || parameters.length)} 条匹配参数`)}</span>
      ${vehicleParametersLoading ? "<span>读取中...</span>" : ""}
    </div>
  `;
}

function renderConnectionsList() {
  normalizeSystemSettingsCopy();
  const list = els.connectionsList;
  if (!list) return;
  const connections = Array.isArray(connectionsCache) ? connectionsCache : [];
  if (!connections.length) {
    list.innerHTML = `<div class="empty" style="padding:12px 4px;color:var(--faint);font-size:12px;text-align:center;">No links yet. Add one below.</div>`;
    return;
  }
  list.innerHTML = connections.map((conn) => {
    const activeClass = conn.id === selectedConnectionId ? "active" : "";
    const connectedClass = isConnectionActive(conn.id) ? "connected" : "";
    return `
      <div class="connection-item ${activeClass} ${connectedClass}" data-connection-id="${escapeHtml(conn.id)}" role="button" tabindex="0" aria-pressed="${conn.id === selectedConnectionId ? "true" : "false"}">
        <span class="link-dot"></span>
        <div class="connection-item-info">
          <strong>${escapeHtml(conn.name || "Unnamed link")}</strong>
          <span>${escapeHtml(connectionTypeLabel(conn.type))} / ${escapeHtml(connectionParamsSummary(conn.params || {}, conn.type))}</span>
        </div>
        <span class="link-chevron">&gt;</span>
      </div>
    `;
  }).join("");
}

function renderConnectionDetail(connectionId) {
  normalizeSystemSettingsCopy();
  selectedConnectionId = connectionId || "";
  renderConnectionsList();

  const conn = selectedConnectionId ? connectionsCache.find((c) => c.id === selectedConnectionId) : null;
  const form = els.connectionDetailForm;
  if (!form) return;

  if (!conn) {
    form.reset();
    if (els.connectionDetailId) els.connectionDetailId.value = "";
    if (els.connectionDetailType) els.connectionDetailType.value = "udp";
    updateConnectionDetailStatus(false);
    if (els.connectionDetailConnect) els.connectionDetailConnect.textContent = "连接";
    if (els.connectionDetailDelete) els.connectionDetailDelete.hidden = true;
    updateConnectionTypeFields();
    renderActualLinkCard();
    renderAirSimSettingsForConnection();
    return;
  }

  const params = conn.params || {};
  if (els.connectionDetailId) els.connectionDetailId.value = conn.id || "";
  if (els.connectionDetailName) els.connectionDetailName.value = conn.name || "";
  if (els.connectionDetailType) els.connectionDetailType.value = conn.type || "udp";
  if (els.connectionDetailPort) els.connectionDetailPort.value = params.port || "";
  if (els.connectionDetailHost) els.connectionDetailHost.value = params.host || params.url || "";
  if (els.connectionDetailAddress) els.connectionDetailAddress.value = params.address || "";
  if (els.connectionDetailPortNumber) els.connectionDetailPortNumber.value = params.portNumber || params.baud || "";
  if (els.connectionDetailRemotePort) els.connectionDetailRemotePort.value = params.remotePort || "";
  if (els.connectionDetailRealVehicle) els.connectionDetailRealVehicle.checked = Boolean(params.realVehicle);
  updateConnectionTypeFields();

  const actuallyActive = isConnectionActive(conn.id);
  updateConnectionDetailStatus(actuallyActive);
  if (els.connectionDetailConnect) els.connectionDetailConnect.textContent = actuallyActive ? "断开" : "连接";
  if (els.connectionDetailDelete) els.connectionDetailDelete.hidden = false;
  renderActualLinkCard();
  // 切换预设后, AirSim settings.json 模板按当前 type 重新计算
  renderAirSimSettingsForConnection();
}

function updateConnectionDetailStatus(connected) {
  if (!els.connectionDetailStatus) return;
  els.connectionDetailStatus.classList.toggle("connected", Boolean(connected));
  const text = els.connectionDetailStatus.querySelector(".status-text");
  if (text) text.textContent = connected ? "已连接" : "未连接";
}

function updateConnectionTypeFields() {
  normalizeSystemSettingsCopy();
  const type = els.connectionDetailType ? els.connectionDetailType.value : "udp";
  const serialFields = document.getElementById("serialFieldsDetail");
  const udpFields = document.getElementById("udpFieldsDetail");
  const tcpFields = document.getElementById("tcpFieldsDetail");
  const portFields = document.getElementById("portFieldsDetail");
  const remotePortFields = document.getElementById("remotePortFieldsDetail");
  const realVehicleFields = document.getElementById("realVehicleFieldsDetail");
  const usesHostLikeField = ["udp", "airsim", "auto", "px4_ros2"].includes(type);
  if (serialFields) serialFields.hidden = type !== "serial";
  if (udpFields) udpFields.hidden = !usesHostLikeField;
  if (tcpFields) tcpFields.hidden = type !== "tcp";
  if (remotePortFields) remotePortFields.hidden = type !== "udp" && type !== "auto";
  if (realVehicleFields) realVehicleFields.hidden = type === "airsim" || type === "px4_ros2";
  const hostLabel = udpFields?.querySelector("label");
  if (hostLabel) {
    hostLabel.textContent = type === "px4_ros2"
      ? "ROS Gateway URL"
      : type === "airsim"
        ? "AirSim host"
        : "PX4 host";
  }
  if (els.connectionDetailHost) {
    els.connectionDetailHost.placeholder = type === "px4_ros2"
      ? "http://127.0.0.1:8766"
      : type === "airsim"
        ? "127.0.0.1"
        : "127.0.0.1 or vehicle IP";
  }
  if (portFields) {
    const label = portFields.querySelector("label");
    if (label) {
      label.textContent = type === "serial"
        ? "Baud"
        : type === "airsim"
          ? "AirSim port"
          : "Local port";
    }
    if (els.connectionDetailPortNumber) {
      els.connectionDetailPortNumber.placeholder = type === "serial"
        ? "57600, 115200, or 921600"
        : type === "airsim"
          ? "41452"
          : "14550, 14540, or 5760";
    }
    portFields.hidden = !["serial", "udp", "tcp", "airsim", "auto"].includes(type);
  }
  // 连接类型切换后, AirSim settings.json 模板区按当前 type 联动
  if (typeof renderAirSimSettingsForConnection === "function") {
    renderAirSimSettingsForConnection();
  }
}

function readConnectionDetailForm() {
  const id = String(els.connectionDetailId?.value || "").trim();
  const name = String(els.connectionDetailName?.value || "").trim();
  const type = String(els.connectionDetailType?.value || "udp").trim();
  if (!name) {
    return { ok: false, error: "Connection name is required." };
  }
  const existing = id ? connectionsCache.find((c) => c.id === id) : null;
  const params = {};
  if (type === "serial") params.port = String(els.connectionDetailPort?.value || "").trim();
  if (type === "udp" || type === "airsim" || type === "auto") params.host = String(els.connectionDetailHost?.value || "").trim();
  if (type === "px4_ros2") {
    params.url = normalizeRosGatewayUrl(els.connectionDetailHost?.value || existing?.params?.url);
    params.workspace = String(existing?.params?.workspace || "$HOME/ws_px4").trim();
  }
  if (type === "tcp") params.address = String(els.connectionDetailAddress?.value || "").trim();
  if (type === "serial") params.baud = String(els.connectionDetailPortNumber?.value || "").trim();
  if (type === "udp" || type === "tcp" || type === "airsim" || type === "auto") {
    params.portNumber = String(els.connectionDetailPortNumber?.value || "").trim();
  }
  if (type === "udp" || type === "auto") params.remotePort = String(els.connectionDetailRemotePort?.value || "").trim();
  if (type !== "airsim" && type !== "px4_ros2") params.realVehicle = Boolean(els.connectionDetailRealVehicle?.checked);

  return {
    ok: true,
    connection: {
      id: id || `conn_${Date.now()}`,
      name,
      type,
      params,
    },
    isNew: !id,
  };
}

async function persistConnectionDetailFromForm({ notify = true } = {}) {
  const read = readConnectionDetailForm();
  if (!read.ok) {
    showNotice(read.error || "Connection settings are invalid.", "error");
    return null;
  }
  const connection = read.connection;
  const id = String(connection.id || "").trim();
  if (id) {
    const idx = connectionsCache.findIndex((c) => c.id === id);
    if (idx >= 0) {
      connectionsCache[idx] = { ...connectionsCache[idx], ...connection };
    } else {
      connectionsCache.push(connection);
    }
  }
  selectedConnectionId = id;
  const saved = await saveConnectionSettings();
  if (!saved) return null;
  renderConnectionsList();
  renderConnectionDetail(selectedConnectionId);
  if (notify) showNotice(read.isNew ? "Connection added." : "Connection updated.", "success");
  return connectionsCache.find((c) => c.id === selectedConnectionId) || connection;
}

async function submitConnectionDetail(event) {
  event.preventDefault();
  await persistConnectionDetailFromForm({ notify: true });
}

function connectionFailureHint(conn, backend, toolData = {}) {
  const params = conn?.params || {};
  const attemptedUrl = toolData.url || toolData.ros_bridge_url || params.url || params.host || "";
  if (backend === "px4_ros2") {
    const healthUrl = String(attemptedUrl || "http://127.0.0.1:8766").replace(/\/+$/, "") + "/health";
    return `检查 Windows 是否能访问 ${healthUrl}。`;
  }
  if (backend === "px4_mavlink") {
    const endpoint = toolData.requested_url || toolData.url || params.host || "selected endpoint";
    return `检查 PX4 SITL MAVLink 端口和所选端点 ${endpoint}。`;
  }
  if (backend === "airsim") {
    const host = params.host || "127.0.0.1";
    const port = params.portNumber || "41452";
    return `检查 AirSim 是否运行在 ${host}:${port}。`;
  }
  return "检查所选连接参数。";
}

async function activateSelectedConnection() {
  if (!selectedConnectionId) return;
  let conn = connectionsCache.find((c) => c.id === selectedConnectionId);
  if (!conn) return;

  const actuallyActive = isConnectionActive(conn.id);
  if (!actuallyActive) {
    const formId = String(els.connectionDetailId?.value || "").trim();
    if (!formId || formId === conn.id) {
      const savedConn = await persistConnectionDetailFromForm({ notify: false });
      if (!savedConn) return;
      conn = savedConn;
    }
  }
  showNotice(actuallyActive ? "Disconnecting..." : `Connecting ${conn.name}...`, "info");

  try {
    // 面板认定的活动链路是按真实端点识别出来的，未必等于 settings 里记的
    // active_connection_id；断开必须走显式接口，否则会被当成"重连这条"。
    const resp = await fetch(
      actuallyActive ? "/api/settings/connections/deactivate" : "/api/settings/connections/activate",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(actuallyActive ? {} : { connection_id: conn.id }),
      }
    );
    const result = await resp.json();
    const toolData = result?.result?.data || {};
    const backend = result?.backend || toolData.backend || "";
    const backendLabel = backendLabelFromId(backend);

    if (result && result.ok && result.action === "disconnect") {
      activeConnectionId = "";
      showNotice("Disconnected.", "info");
    } else if (result && result.ok && result.result && result.result.ok) {
      activeConnectionId = conn.id;
      showNotice(`Connected to ${conn.name}.`, "success");
    } else {
      activeConnectionId = "";
      const rawMsg = toolData.message || result?.error || "";
      const hint = connectionFailureHint(conn, backend, toolData);
      showNotice(rawMsg ? `${rawMsg} - ${hint}` : `${backendLabel} connection failed - ${hint}`, "error");
    }
  } catch (error) {
    activeConnectionId = "";
    showNotice(error.message || "Connection switch failed.", "error");
  }
  await loadConnectionSettings(true);
  if (activeSystemSettingsSection === "parameters") {
    await loadVehicleParameters(false);
  }
  renderConnectionsList();
  renderConnectionDetail(selectedConnectionId);
  await refresh();
}

function renderSystemConnection(drone = {}, toolRuntime = {}) {
  const connected = Boolean(toolRuntime.connected) && !toolRuntime.stale_connection;
  let activeChanged = false;

  if (connected && connectionsCache.length) {
    // 谁在连着由实际链路决定：按监听口 + 真实心跳来源认领对应的连接。
    // 以前这里退化成"列表里第一条同后端的预设"，于是用户新加的 127.0.0.1
    // 链路会被显示成连在那条老的 JETSON 预设上。
    const identified = refreshLiveConnectionId(currentActualLink(), connected);
    if (identified && identified !== activeConnectionId) {
      activeConnectionId = identified;
      activeChanged = true;
    }
  } else if (activeConnectionId) {
    activeConnectionId = "";
    activeChanged = true;
  }

  if (activeChanged) {
    renderConnectionsList();
    if (selectedConnectionId) renderConnectionDetail(selectedConnectionId);
  }
  if (els.connectionDetailStatus) {
    const actuallyActive = selectedConnectionId ? isConnectionActive(selectedConnectionId) : connected;
    updateConnectionDetailStatus(actuallyActive);
  }
  renderActualLinkCard();
  if (activeSystemSettingsSection === "parameters") {
    renderVehicleParametersPanel();
  }
}

