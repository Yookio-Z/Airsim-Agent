// ui-telemetry.js —— HUD 指标、遥测表格与曲线
// 由 app.js 拆分而来；各文件共享同一份脚本作用域，按顺序加载。

async function loadVehicleTelemetry(force = false) {
  if (vehicleTelemetryLoading && !force) return vehicleSetupCache;
  if (!isRealtimeVehicleSetupSection()) return vehicleSetupCache;
  vehicleTelemetryLoading = true;
  const now = Date.now();
  const includeHistory = force || now - vehicleHistoryLastFetchAt >= 250;
  let historyLimit = 180;
  if (activeSystemSettingsSection === "waveforms") {
    historyLimit = Math.max(180, activeWaveformWindowSec * Math.min(activeWaveformSampleHz, 20));
  } else if (activeSystemSettingsSection === "pid_tuning") {
    historyLimit = 240;
  }
  historyLimit = Math.min(2400, historyLimit);
  const historyGroups = activeVehicleHistoryGroups();
  const params = new URLSearchParams({
    history: includeHistory ? "1" : "0",
    limit: String(historyLimit),
    groups: historyGroups.join(","),
  });
  try {
    const data = await api(`/api/settings/vehicle-telemetry?${params.toString()}`);
    mergeVehicleSetupSnapshot(data.vehicle_telemetry || data || {}, { replace: false });
    if (includeHistory) vehicleHistoryLastFetchAt = now;
    renderActiveVehicleSetupPanel("telemetry");
    return vehicleSetupCache;
  } catch (error) {
    if (!vehicleSetupCache) {
      mergeVehicleSetupSnapshot({
        status: "error",
        connected: false,
        message: error.message || "vehicle telemetry unavailable",
        history: {},
      }, { replace: true });
    }
    renderActiveVehicleSetupPanel("telemetry-error");
    return vehicleSetupCache;
  } finally {
    vehicleTelemetryLoading = false;
  }
}

function activeVehicleHistoryGroups() {
  if (activeSystemSettingsSection === "sensors") return ["attitude", "imu", "vibration"];
  if (activeSystemSettingsSection === "pid_tuning") {
    return [pidChartConfig(activePidTuningView).history];
  }
  if (activeSystemSettingsSection === "waveforms") {
    return [...new Set(selectedWaveformChannels().map((channel) => channel.history))];
  }
  if (activeSystemSettingsSection === "radio") return ["rc"];
  if (activeSystemSettingsSection === "power") return ["battery"];
  if (activeSystemSettingsSection === "actuators") return ["servo"];
  return ["attitude", "position", "battery"];
}

function restartVehicleTelemetryPolling() {
  if (vehicleTelemetryPollTimer) {
    clearInterval(vehicleTelemetryPollTimer);
    vehicleTelemetryPollTimer = null;
  }
  if (els.systemSettingsModal?.hidden || !isVehicleSetupSection() || !isRealtimeVehicleSetupSection()) return;
  loadVehicleTelemetry(true).catch(() => {});
  vehicleTelemetryPollTimer = setInterval(() => {
    if (els.systemSettingsModal?.hidden || !isVehicleSetupSection() || !isRealtimeVehicleSetupSection()) {
      restartVehicleTelemetryPolling();
      return;
    }
    loadVehicleTelemetry(false).catch(() => {});
  }, Math.max(50, Number(applicationSettings.telemetry.setup_refresh_ms || VEHICLE_TELEMETRY_POLL_MS)));
}

// 只更新芯片选中态，不重建 DOM（避免上方状态栏闪烁）
function updateChipStates() {
  document.querySelectorAll("#vehicleList .hud-vehicle-chip[data-vehicle]").forEach((chip) => {
    chip.classList.toggle("selected", chip.dataset.vehicle === controlSelectionVehicle);
  });
}

async function refresh() {
  latestState = applyCachedSessionHistory(await api("/api/state"));
  render(latestState);
  loadCurrentSessionHistory().catch(() => {});
  syncRosTelemetryStream();
}

async function refreshTelemetryOnly() {
  syncRosTelemetryStream();
  if (rosTelemetryConnected) return;
  if (telemetryRefreshInFlight) return;
  telemetryRefreshInFlight = true;
  try {
    const frame = await api("/api/telemetry");
    latestState = latestState || {};
    latestState.runtime = frame.runtime || latestState.runtime;
    latestState.supervisor = frame.supervisor || latestState.supervisor;
    latestState.tool_runtime = frame.tool_runtime || latestState.tool_runtime;
    latestState.current_run = frame.current_run ?? latestState.current_run;
    latestState.llm = frame.llm || latestState.llm;
    const run = latestState.current_run;
    const toolRuntime = latestState.tool_runtime || {};
    const drone = toolRuntime.drone || {};
    renderTopbar(run, toolRuntime, latestState.supervisor || {}, latestState.llm || {});
    renderTelemetry(drone, toolRuntime);
    updateMapView(latestState);
    checkFlightTaskCompletion(toolRuntime);
    checkReturnHomeCompletion(toolRuntime);
  } finally {
    telemetryRefreshInFlight = false;
  }
}

function restartMainTelemetryRefresh() {
  window.clearTimeout(telemetryRefreshTimer);
  const tick = async () => {
    try {
      await refreshTelemetryOnly();
    } catch (_) {
      // The next scheduled poll will retry.
    } finally {
      const interval = Math.max(100, Number(applicationSettings.telemetry.refresh_ms || 250));
      telemetryRefreshTimer = window.setTimeout(tick, interval);
    }
  };
  const interval = Math.max(100, Number(applicationSettings.telemetry.refresh_ms || 250));
  telemetryRefreshTimer = window.setTimeout(tick, interval);
}

function applyCachedSessionHistory(snapshot) {
  const sessionId = snapshot?.current_session?.id || "";
  if (!sessionId || !fullSessionMessageCache.has(sessionId)) return snapshot;
  const merged = mergeSessionMessages(fullSessionMessageCache.get(sessionId), snapshot.messages);
  fullSessionMessageCache.set(sessionId, merged);
  snapshot.messages = merged;
  return snapshot;
}

async function loadCurrentSessionHistory(force = false) {
  const sessionId = latestState?.current_session?.id || "";
  if (!sessionId || sessionHistoryLoading.has(sessionId)) return;
  if (!force && fullSessionMessageCache.has(sessionId)) {
    applyCachedSessionHistory(latestState);
    renderChat(latestState.messages || [], latestState.current_run, latestState.llm || {});
    return;
  }
  sessionHistoryLoading.add(sessionId);
  try {
    const result = await api(`/api/sessions/${encodeURIComponent(sessionId)}/history`);
    const messages = Array.isArray(result?.session?.messages) ? result.session.messages : [];
    fullSessionMessageCache.set(sessionId, messages);
    if (latestState?.current_session?.id === sessionId) {
      latestState.messages = mergeSessionMessages(messages, latestState.messages);
      renderChat(latestState.messages || [], latestState.current_run, latestState.llm || {});
    }
  } catch (_) {
    // The live snapshot remains usable when persisted history cannot be loaded.
  } finally {
    sessionHistoryLoading.delete(sessionId);
  }
}

function renderTelemetry(drone, toolRuntime) {
  const pos = drone.position_ned || { x: 0, y: 0, z: 0 };
  const vel = drone.velocity_ned || { vx: 0, vy: 0, vz: 0 };
  const speed = Math.hypot(Number(vel.vx || 0), Number(vel.vy || 0), Number(vel.vz || 0));
  const altitude = Math.max(0, Math.abs(Number(pos.z || 0)));
  const connected = Boolean(toolRuntime.connected) && !toolRuntime.stale_connection;
  const realVehicle = isRealVehicleRuntime(toolRuntime);
  const reliableNavPosition = drone.navigation_position_valid === true || (!realVehicle && drone.navigation_position_valid !== false);

  renderVehicleList(toolRuntime);

  if (els.vehicleState) {
    els.vehicleState.textContent = drone.armed ? "ARMED" : "DISARMED";
    els.vehicleState.classList.toggle("armed", Boolean(drone.armed));
  }
  if (els.metricAltitude) els.metricAltitude.textContent = reliableNavPosition ? `${fmt(altitude)} m` : "--";
  if (els.metricPosition) {
    els.metricPosition.textContent = reliableNavPosition
      ? `N ${fmt(pos.x)} / E ${fmt(pos.y)} / D ${fmt(pos.z)}`
      : `NED RAW N ${fmt(pos.x)} / E ${fmt(pos.y)} / D ${fmt(pos.z)}`;
    els.metricPosition.title = reliableNavPosition
      ? "可用于导航的位置"
      : "真实飞控未解锁/未飞行时 LOCAL_POSITION_NED 可能漂移，当前不用于地图或距离计算";
  }
  if (els.metricVelocity) els.metricVelocity.textContent = `${fmt(speed)} m/s`;
  if (els.metricBattery) els.metricBattery.textContent = drone.battery_voltage != null ? `${fmt(drone.battery_voltage)} V` : (realVehicle ? "--" : "SIM");
  if (els.metricFlight) els.metricFlight.textContent = connected ? (drone.flying ? "空中" : "地面") : "离线";
  if (els.metricWaypoint) els.metricWaypoint.textContent = `#${missionWaypoints.length || extractPlanWaypoints(latestState?.current_run).length || 0}`;

  renderMissionMetrics(drone);
  renderSystemConnection(drone, toolRuntime);
}

function angleDeltaDeg(a, b) {
  return Math.abs((((a - b) + 540) % 360) - 180);
}

function formatSigned(value, unit) {
  if (value == null || Number.isNaN(value)) return "--";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${fmt(Math.abs(value))}${unit ? ` ${unit}` : ""}`;
}

function rosTelemetryStreamBaseUrl() {
  const runtime = latestState?.tool_runtime || {};
  const backend = String(runtime.backend || runtime.backend_profile?.id || "").toLowerCase();
  if (backend !== "px4_ros2") return "";
  if (!runtime.connected || runtime.stale_connection) return "";
  const drone = runtime.drone || {};
  const activeLink = drone.active_link || currentActualLink() || {};
  const activeConnection = connectionsCache.find((connection) => connection.id === activeConnectionId) || null;
  const url =
    activeLink.url ||
    activeLink.ros_bridge_url ||
    runtime.backend_profile?.capabilities?.ros_bridge_url ||
    activeConnection?.params?.url ||
    "";
  return normalizeRosGatewayUrl(url);
}

function rosTelemetryStreamUrl() {
  const baseUrl = rosTelemetryStreamBaseUrl();
  if (!baseUrl) return "";
  return `${baseUrl.replace(/\/+$/, "")}/providers/px4/telemetry/stream?hz=20`;
}

function closeRosTelemetryStream() {
  window.clearTimeout(rosTelemetryReconnectTimer);
  rosTelemetryReconnectTimer = null;
  rosTelemetryConnected = false;
  rosTelemetryUrl = "";
  if (latestState?.tool_runtime) {
    latestState.tool_runtime.telemetry_stream_active = false;
  }
  if (rosTelemetrySource) {
    rosTelemetrySource.close();
    rosTelemetrySource = null;
  }
}

function syncRosTelemetryStream() {
  if (!window.EventSource) return;
  const url = rosTelemetryStreamUrl();
  if (!url) {
    closeRosTelemetryStream();
    return;
  }
  if (rosTelemetrySource && rosTelemetryUrl === url) return;
  closeRosTelemetryStream();
  rosTelemetryUrl = url;
  rosTelemetrySource = new EventSource(url);
  rosTelemetrySource.addEventListener("telemetry", handleRosTelemetryEvent);
  rosTelemetrySource.onerror = () => {
    if (rosTelemetrySource) {
      rosTelemetrySource.close();
      rosTelemetrySource = null;
    }
    rosTelemetryConnected = false;
    window.clearTimeout(rosTelemetryReconnectTimer);
    rosTelemetryReconnectTimer = window.setTimeout(syncRosTelemetryStream, 1500);
  };
}

function handleRosTelemetryEvent(event) {
  const payload = parseStreamData(event);
  const data = payload?.data && typeof payload.data === "object" ? payload.data : {};
  if (!latestState || !data) return;
  const runtime = latestState.tool_runtime || {};
  const backend = String(runtime.backend || runtime.backend_profile?.id || "").toLowerCase();
  if (backend !== "px4_ros2") return;
  const previousDrone = runtime.drone || {};
  latestState.tool_runtime = {
    ...runtime,
    connected: payload.ok !== false,
    stale_connection: false,
    telemetry_stream_active: true,
    telemetry_stream_received_at_ms: Date.now(),
    drone: {
      ...previousDrone,
      ...data,
      active_link: previousDrone.active_link || currentActualLink(),
    },
  };
  rosTelemetryConnected = true;
  const toolRuntime = latestState.tool_runtime || {};
  const drone = toolRuntime.drone || {};
  renderTopbar(latestState.current_run, toolRuntime, latestState.supervisor || {}, latestState.llm || {});
  renderTelemetry(drone, toolRuntime);
  updateMapView(latestState);
  renderActualLinkCard();
}

function formatSignedNumber(value, digits = 1, unit = "") {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  const prefix = number > 0 ? "+" : "";
  return `${prefix}${number.toFixed(digits)}${unit}`;
}

function readOnlyRibbon() {
  const setup = setupSnapshot();
  const msg = setup.read_only?.message || "当前设置页已开放单参数写入；校准、电机测试和固件烧录暂未开放。";
  return `<div class="readonly-ribbon">${escapeHtml(msg)}</div>`;
}

function dataSourceRibbon(items = []) {
  const labels = items.filter(Boolean);
  if (!labels.length) return "";
  return `<div class="data-source-ribbon">${labels.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>`;
}

function renderUnavailableSetup(panel, title = "未连接 PX4") {
  if (!panel) return true;
  if (setupConnected()) return false;
  panel.dataset.pidMounted = "";
  panel.dataset.sensorMounted = "";
  panel.dataset.waveformMounted = "";
  panel.innerHTML = `<div class="setup-empty"><strong>${escapeHtml(title)}</strong><span>连接真实 PX4 后这里会显示实时配置与遥测。</span></div>`;
  return true;
}

function axisMeter(label, value, min, max, unit = "", digits = 1) {
  const number = value === null || value === undefined || value === "" ? NaN : Number(value);
  const finite = Number.isFinite(number);
  const pct = finite ? Math.max(0, Math.min(100, ((number - min) / (max - min)) * 100)) : 0;
  return `
    <div class="axis-meter">
      <span>${escapeHtml(label)}</span>
      <div class="axis-track"><i style="width:${pct}%"></i></div>
      <strong>${finite ? `${number.toFixed(digits)}${unit}` : "--"}</strong>
    </div>
  `;
}

function axisPill(label, value, color, digits = 2) {
  const number = value === null || value === undefined || value === "" ? NaN : Number(value);
  return `
    <div class="axis-pill" style="--axis-color:${escapeHtml(color)}">
      <span>${escapeHtml(label)}</span>
      <strong>${Number.isFinite(number) ? number.toFixed(digits) : "--"}</strong>
    </div>
  `;
}

function drawVehicleSensorMiniCharts() {
  const history = setupSnapshot().history || {};
  drawMiniHistoryCanvas("sensorAccelCanvas", history.imu || [], [
    { field: "xacc", label: "X", color: WAVEFORM_COLORS[7] },
    { field: "yacc", label: "Y", color: WAVEFORM_COLORS[1] },
    { field: "zacc", label: "Z", color: WAVEFORM_COLORS[6] },
  ]);
  drawMiniHistoryCanvas("sensorGyroCanvas", history.imu || [], [
    { field: "xgyro", label: "X", color: WAVEFORM_COLORS[3] },
    { field: "ygyro", label: "Y", color: WAVEFORM_COLORS[4] },
    { field: "zgyro", label: "Z", color: WAVEFORM_COLORS[0] },
  ]);
  drawMiniHistoryCanvas("sensorMagCanvas", history.imu || [], [
    { field: "xmag", label: "X", color: WAVEFORM_COLORS[0] },
    { field: "ymag", label: "Y", color: WAVEFORM_COLORS[1] },
    { field: "zmag", label: "Z", color: WAVEFORM_COLORS[2] },
  ]);
}

function renderVehicleSensorsPanel(force = false) {
  const panel = els.vehicleSensorsPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const now = performance.now();
  if (!force && panel.dataset.sensorMounted === "1" && now - vehicleSensorsLastRenderAt < VEHICLE_SENSOR_RENDER_THROTTLE_MS) {
    return;
  }
  vehicleSensorsLastRenderAt = now;
  panel.dataset.sensorMounted = "1";
  const setup = setupSnapshot();
  const telemetry = setup.telemetry || {};
  const attitude = telemetry.attitude || {};
  const imu = telemetry.imu || {};
  const vibration = telemetry.vibration || {};
  const gpsRaw = telemetry.gps_raw || {};
  const globalPosition = telemetry.global_position || {};
  const sensors = setup.summary?.sensors || {};
  const health = telemetry.sensor_health?.items || {};
  const roll = Number(attitude.roll_deg || 0);
  const pitch = Number(attitude.pitch_deg || 0);
  const horizonShift = Math.max(-36, Math.min(36, pitch * 1.4));
  const imuUnit = String(imu.unit || "");
  const scaledImu = imuUnit.includes("mG") || String(sensors.latest_imu_source || "").startsWith("SCALED") || sensors.latest_imu_source === "RAW_IMU";
  const accelRange = scaledImu ? 2200 : 22;
  const gyroRange = scaledImu ? 6000 : 7;
  const magRange = scaledImu ? 2000 : 2;
  const sensorTabs = [
    ["imu", "IMU"],
    ["mag", "罗盘"],
    ["gps", "GPS"],
    ["flow", "光流"],
    ["rangefinder", "测距仪"],
  ];
  const tabsHtml = `
    <div class="sensor-config-tabs">
      ${sensorTabs.map(([id, label]) => `<button type="button" data-sensor-setup-tab="${id}" class="${activeSensorSetupTab === id ? "active" : ""}">${escapeHtml(label)}</button>`).join("")}
    </div>
  `;
  const statusStrip = `
    <div class="sensor-status-strip">
      ${Object.entries({
        gyro: "陀螺仪",
        accel: "加速度计",
        mag: "磁罗盘",
        baro: "气压计",
        gps: "GPS",
      }).map(([key, label]) => `${setupBadge(health[key]?.healthy ? "ready" : (health[key]?.present ? "warning" : "missing"), label)}`).join("")}
    </div>
  `;
  const imuView = `
    <div class="sensor-mico-grid">
      <section class="setup-detail-card sensor-wave-card">
        <header><strong>加速度计</strong><span>${escapeHtml(imu.unit || sensors.latest_imu_source || "--")}</span></header>
        <div class="sensor-axis-readouts">
          ${axisPill("X", imu.xacc, WAVEFORM_COLORS[7])}
          ${axisPill("Y", imu.yacc, WAVEFORM_COLORS[1])}
          ${axisPill("Z", imu.zacc, WAVEFORM_COLORS[6])}
        </div>
        <canvas id="sensorAccelCanvas" class="sensor-wave-canvas"></canvas>
      </section>
      <section class="setup-detail-card sensor-wave-card">
        <header><strong>陀螺仪</strong><span>${scaledImu ? "raw / scaled" : "rad/s"}</span></header>
        <div class="sensor-axis-readouts">
          ${axisPill("X", imu.xgyro, WAVEFORM_COLORS[3])}
          ${axisPill("Y", imu.ygyro, WAVEFORM_COLORS[4])}
          ${axisPill("Z", imu.zgyro, WAVEFORM_COLORS[0])}
        </div>
        <canvas id="sensorGyroCanvas" class="sensor-wave-canvas"></canvas>
      </section>
      <section class="setup-detail-card sensor-orientation-card">
        <strong>姿态</strong>
        <div class="attitude-widget compact">
          <div class="attitude-horizon" style="transform: translateY(${horizonShift}px) rotate(${-roll}deg)"></div>
          <div class="attitude-aircraft"></div>
          <span class="attitude-readout roll">Roll ${escapeHtml(formatSignedNumber(roll, 1, "°"))}</span>
          <span class="attitude-readout pitch">Pitch ${escapeHtml(formatSignedNumber(pitch, 1, "°"))}</span>
        </div>
        ${statusStrip}
      </section>
      <section class="setup-detail-card">
        <strong>振动</strong>
        <div class="axis-grid">
          ${axisMeter("Vibration X", vibration.vibration_x, 0, 50, "", 2)}
          ${axisMeter("Vibration Y", vibration.vibration_y, 0, 50, "", 2)}
          ${axisMeter("Vibration Z", vibration.vibration_z, 0, 50, "", 2)}
        </div>
        ${setupRows([["Clip 0", vibration.clipping_0], ["Clip 1", vibration.clipping_1], ["Clip 2", vibration.clipping_2]])}
      </section>
    </div>
  `;
  const magView = `
    <div class="sensor-mico-grid">
      <section class="setup-detail-card sensor-wave-card">
        <header><strong>磁罗盘</strong><span>${escapeHtml(sensors.latest_imu_source || imu.source || "--")}</span></header>
        <div class="sensor-axis-readouts">
          ${axisPill("X", imu.xmag, WAVEFORM_COLORS[0])}
          ${axisPill("Y", imu.ymag, WAVEFORM_COLORS[1])}
          ${axisPill("Z", imu.zmag, WAVEFORM_COLORS[2])}
        </div>
        <canvas id="sensorMagCanvas" class="sensor-wave-canvas"></canvas>
      </section>
      <section class="setup-detail-card">
        <strong>罗盘状态</strong>
        ${setupRows([
          ["磁罗盘", setupStatusLabel(sensors.mag)],
          ["CAL_MAG0_ID", sensors.mag0_id],
          ["板载方向 SENS_BOARD_ROT", sensors.board_rotation],
          ["SYS_STATUS", health.mag?.healthy ? "healthy" : (health.mag?.present ? "present" : "missing")],
        ])}
      </section>
    </div>
  `;
  const gpsView = `
    <div class="sensor-mico-grid">
      <section class="setup-detail-card">
        <strong>GPS</strong>
        ${setupRows([
          ["状态", setupStatusLabel(sensors.gps)],
          ["Fix type", gpsRaw.fix_type],
          ["Satellites", gpsRaw.satellites_visible],
          ["Lat", globalPosition.lat ?? gpsRaw.lat],
          ["Lon", globalPosition.lon ?? gpsRaw.lon],
          ["Alt", formatNumber(globalPosition.relative_alt ?? gpsRaw.alt, 2, " m")],
          ["EPH", gpsRaw.eph],
          ["EPV", gpsRaw.epv],
        ])}
      </section>
      <section class="setup-detail-card">
        <strong>位置可信度</strong>
        ${setupRows([
          ["位置来源", telemetry.status?.position_source],
          ["地图位置", telemetry.status?.map_position_valid ? "可信" : "未采用"],
          ["导航位置", telemetry.status?.navigation_position_valid ? "可信" : "不可用于导航"],
          ["Global age", formatNumber(telemetry.status?.global_position_age_s, 2, " s")],
        ])}
      </section>
    </div>
  `;
  const unavailableView = (title, source) => `
    <section class="setup-detail-card wide">
      <strong>${escapeHtml(title)}</strong>
      <div class="setup-empty small">当前未收到 ${escapeHtml(source)} 对应 MAVLink 数据。连接支持该传感器的飞控后，这里会显示实时值和状态。</div>
    </section>
  `;
  const activeView = {
    imu: imuView,
    mag: magView,
    gps: gpsView,
    flow: unavailableView("光流", "OPTICAL_FLOW / OPTICAL_FLOW_RAD"),
    rangefinder: unavailableView("测距仪", "DISTANCE_SENSOR"),
  }[activeSensorSetupTab] || imuView;
  panel.innerHTML = `
    ${tabsHtml}
    ${activeView}
    <div class="sensor-config-footer">
      <section class="setup-detail-card">
        <strong>校准状态</strong>
        ${setupRows([
          ["陀螺仪", setupStatusLabel(sensors.gyro)],
          ["加速度计", setupStatusLabel(sensors.accel)],
          ["磁罗盘", setupStatusLabel(sensors.mag)],
          ["气压计", setupStatusLabel(sensors.baro)],
          ["板载方向 SENS_BOARD_ROT", sensors.board_rotation],
          ["CAL_GYRO0_ID", sensors.gyro0_id],
          ["CAL_ACC0_ID", sensors.acc0_id],
          ["CAL_MAG0_ID", sensors.mag0_id],
        ])}
      </section>
    </div>
    ${dataSourceRibbon(["ATTITUDE", sensors.latest_imu_source || "IMU", "SYS_STATUS", "VIBRATION"])}
    ${readOnlyRibbon()}
  `;
  requestAnimationFrame(drawVehicleSensorMiniCharts);
}

function channelBar(label, value, min = 900, max = 2100) {
  const number = value === null || value === undefined || value === "" ? NaN : Number(value);
  const finite = Number.isFinite(number);
  const pct = finite ? Math.max(0, Math.min(100, ((number - min) / (max - min)) * 100)) : 0;
  return `
    <div class="channel-bar">
      <span>${escapeHtml(label)}</span>
      <div><i style="width:${pct}%"></i></div>
      <strong>${finite ? String(Math.round(number)) : "--"}</strong>
    </div>
  `;
}

function renderVehiclePidPanel(force = false) {
  const panel = els.vehiclePidPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const views = [
    ["rate_roll", "Rate Roll"],
    ["rate_pitch", "Rate Pitch"],
    ["rate_yaw", "Rate Yaw"],
    ["att_roll", "Att Roll"],
    ["att_pitch", "Att Pitch"],
    ["vel_xy", "Velocity"],
    ["pos_xy", "Position"],
  ];
  if (force || panel.dataset.pidMounted !== "1") {
    panel.innerHTML = `
      <div class="pid-layout">
        <section class="setup-detail-card pid-chart-card">
          <div class="pid-tab-row">
            ${views.map(([id, label]) => `<button type="button" data-pid-view="${id}" class="${activePidTuningView === id ? "active" : ""}">${escapeHtml(label)}</button>`).join("")}
          </div>
          <canvas id="vehiclePidCanvas" class="pid-canvas"></canvas>
          <div class="pid-legend">
            <span><i class="response"></i>Response</span>
            <span><i class="setpoint"></i>Setpoint</span>
          </div>
        </section>
        <aside class="setup-detail-card pid-side">
          <strong>只读调参观察 <span id="vehiclePidSource"></span></strong>
          <div id="vehiclePidReadouts"></div>
        </aside>
      </div>
      ${dataSourceRibbon(["ATTITUDE", "ATTITUDE_TARGET", "PARAM_VALUE"])}
      ${readOnlyRibbon()}
    `;
    panel.dataset.pidMounted = "1";
  }
  updateVehiclePidPanelChrome();
  requestAnimationFrame(drawPidTuningChart);
}

function updateVehiclePidPanelChrome() {
  const panel = els.vehiclePidPanel;
  if (!panel) return;
  panel.querySelectorAll("[data-pid-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.pidView === activePidTuningView);
  });
  const setup = setupSnapshot();
  const config = pidChartConfig(activePidTuningView);
  const history = setup.history || {};
  const points = Array.isArray(history[config.history]) ? history[config.history] : [];
  const source = document.getElementById("vehiclePidSource");
  if (source) source.textContent = `${config.title} / ${points.length} 点`;
  const rows = document.getElementById("vehiclePidReadouts");
  if (!rows) return;
  const live = currentPidLiveValues(config);
  rows.innerHTML = setupRows([
    ["数据源", config.history === "rate" ? "ATTITUDE + ATTITUDE_TARGET" : config.history.toUpperCase()],
    ["Response 当前值", formatNumber(live.response, 2, ` ${config.unit}`)],
    ["Setpoint 当前值", live.setpoint == null ? "--" : formatNumber(live.setpoint, 2, ` ${config.unit}`)],
    ["Airmode MC_AIRMODE", parameterValueFromCache("MC_AIRMODE")],
    ["Thrust curve THR_MDL_FAC", parameterValueFromCache("THR_MDL_FAC")],
    ["Roll rate K", parameterValueFromCache("MC_ROLLRATE_K")],
    ["Pitch rate K", parameterValueFromCache("MC_PITCHRATE_K")],
    ["Yaw rate K", parameterValueFromCache("MC_YAWRATE_K")],
  ]);
}

function currentPidLiveValues(config) {
  const telemetry = setupSnapshot().telemetry || {};
  const attitude = telemetry.attitude || {};
  const target = telemetry.attitude_target || {};
  const position = telemetry.position || {};
  const map = {
    roll: [attitude.rollspeed_deg_s, target.body_roll_rate_deg_s],
    pitch: [attitude.pitchspeed_deg_s, target.body_pitch_rate_deg_s],
    yaw: [attitude.yawspeed_deg_s, target.body_yaw_rate_deg_s],
    att_roll: [attitude.roll_deg, target.roll_deg],
    att_pitch: [attitude.pitch_deg, target.pitch_deg],
    vx: [position.vx, null],
    x: [position.x, null],
  };
  const key = config.liveKey || config.response;
  const values = map[key] || [null, null];
  const asFinite = (value) => (
    value === null || value === undefined || value === "" ? null : (Number.isFinite(Number(value)) ? Number(value) : null)
  );
  return {
    response: asFinite(values[0]),
    setpoint: asFinite(values[1]),
  };
}

function pidChartConfig(view) {
  return {
    rate_roll: { history: "rate", response: "roll", setpoint: "roll_setpoint", liveKey: "roll", title: "Roll Rate", unit: "deg/s", windowSec: 8, defaultRange: [-45, 45] },
    rate_pitch: { history: "rate", response: "pitch", setpoint: "pitch_setpoint", liveKey: "pitch", title: "Pitch Rate", unit: "deg/s", windowSec: 8, defaultRange: [-45, 45] },
    rate_yaw: { history: "rate", response: "yaw", setpoint: "yaw_setpoint", liveKey: "yaw", title: "Yaw Rate", unit: "deg/s", windowSec: 8, defaultRange: [-45, 45] },
    att_roll: { history: "attitude", response: "roll", setpoint: "roll_setpoint", liveKey: "att_roll", title: "Roll Attitude", unit: "deg", windowSec: 10, defaultRange: [-45, 45] },
    att_pitch: { history: "attitude", response: "pitch", setpoint: "pitch_setpoint", liveKey: "att_pitch", title: "Pitch Attitude", unit: "deg", windowSec: 10, defaultRange: [-45, 45] },
    vel_xy: { history: "velocity", response: "vx", setpoint: null, liveKey: "vx", title: "Velocity X", unit: "m/s", windowSec: 10, defaultRange: [-3, 3] },
    pos_xy: { history: "position", response: "x", setpoint: null, liveKey: "x", title: "Local Position X", unit: "m", windowSec: 12, defaultRange: [-5, 5] },
  }[view] || { history: "rate", response: "roll", setpoint: "roll_setpoint", liveKey: "roll", title: "Roll Rate", unit: "deg/s", windowSec: 8, defaultRange: [-45, 45] };
}

function decimateChartSeries(series, maxPoints) {
  const limit = Math.max(40, Math.floor(maxPoints || 600));
  if (!Array.isArray(series) || series.length <= limit) return series;
  const step = Math.ceil(series.length / limit);
  return series.filter((_, index) => index % step === 0 || index === series.length - 1);
}

function robustChartRange(values, fallback = [-1, 1]) {
  const finite = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (!finite.length) return fallback;
  if (finite.length < 8) {
    const min = Math.min(...finite, fallback[0]);
    const max = Math.max(...finite, fallback[1]);
    return min === max ? [min - 1, max + 1] : [min, max];
  }
  const pick = (q) => finite[Math.max(0, Math.min(finite.length - 1, Math.floor((finite.length - 1) * q)))];
  let min = pick(0.04);
  let max = pick(0.96);
  const fallbackSpan = Math.abs((fallback[1] || 1) - (fallback[0] || -1));
  if (!Number.isFinite(min) || !Number.isFinite(max) || Math.abs(max - min) < fallbackSpan * 0.12) {
    min = Math.min(...finite, fallback[0]);
    max = Math.max(...finite, fallback[1]);
  }
  if (min === max) return [min - 1, max + 1];
  return [min, max];
}

function vehicleWaveformGroups() {
  return [
    {
      id: "attitude",
      label: "姿态",
      channels: [
        { key: "attitude.roll", label: "Roll(°)", history: "attitude", field: "roll", unit: "°", color: WAVEFORM_COLORS[0] },
        { key: "attitude.pitch", label: "Pitch(°)", history: "attitude", field: "pitch", unit: "°", color: WAVEFORM_COLORS[1] },
        { key: "attitude.yaw", label: "Yaw(°)", history: "attitude", field: "yaw", unit: "°", color: WAVEFORM_COLORS[2] },
        { key: "rate.roll", label: "Roll Rate", history: "rate", field: "roll", unit: "°/s", color: WAVEFORM_COLORS[3] },
        { key: "rate.pitch", label: "Pitch Rate", history: "rate", field: "pitch", unit: "°/s", color: WAVEFORM_COLORS[4] },
        { key: "rate.yaw", label: "Yaw Rate", history: "rate", field: "yaw", unit: "°/s", color: WAVEFORM_COLORS[5] },
      ],
    },
    {
      id: "flight",
      label: "飞行数据",
      channels: [
        { key: "position.x", label: "Local X", history: "position", field: "x", unit: "m", color: WAVEFORM_COLORS[0] },
        { key: "position.y", label: "Local Y", history: "position", field: "y", unit: "m", color: WAVEFORM_COLORS[1] },
        { key: "position.z", label: "Local Z", history: "position", field: "z", unit: "m", color: WAVEFORM_COLORS[2] },
        { key: "velocity.vx", label: "Vel X", history: "velocity", field: "vx", unit: "m/s", color: WAVEFORM_COLORS[3] },
        { key: "velocity.vy", label: "Vel Y", history: "velocity", field: "vy", unit: "m/s", color: WAVEFORM_COLORS[4] },
        { key: "velocity.vz", label: "Vel Z", history: "velocity", field: "vz", unit: "m/s", color: WAVEFORM_COLORS[5] },
      ],
    },
    {
      id: "battery",
      label: "电池",
      channels: [
        { key: "battery.voltage", label: "Volt(V)", history: "battery", field: "voltage", unit: "V", color: WAVEFORM_COLORS[0] },
        { key: "battery.current", label: "Curr(A)", history: "battery", field: "current", unit: "A", color: WAVEFORM_COLORS[3] },
        { key: "battery.remaining", label: "Batt(%)", history: "battery", field: "remaining", unit: "%", color: WAVEFORM_COLORS[1] },
      ],
    },
    {
      id: "vibration",
      label: "振动",
      channels: [
        { key: "vibration.x", label: "Vibration X", history: "vibration", field: "x", unit: "", color: WAVEFORM_COLORS[0] },
        { key: "vibration.y", label: "Vibration Y", history: "vibration", field: "y", unit: "", color: WAVEFORM_COLORS[1] },
        { key: "vibration.z", label: "Vibration Z", history: "vibration", field: "z", unit: "", color: WAVEFORM_COLORS[2] },
      ],
    },
    {
      id: "accel",
      label: "加速度计",
      channels: [
        { key: "imu.xacc", label: "Accel X", history: "imu", field: "xacc", unit: "", color: WAVEFORM_COLORS[7] },
        { key: "imu.yacc", label: "Accel Y", history: "imu", field: "yacc", unit: "", color: WAVEFORM_COLORS[1] },
        { key: "imu.zacc", label: "Accel Z", history: "imu", field: "zacc", unit: "", color: WAVEFORM_COLORS[6] },
      ],
    },
    {
      id: "gyro",
      label: "陀螺仪",
      channels: [
        { key: "imu.xgyro", label: "Gyro X", history: "imu", field: "xgyro", unit: "", color: WAVEFORM_COLORS[3] },
        { key: "imu.ygyro", label: "Gyro Y", history: "imu", field: "ygyro", unit: "", color: WAVEFORM_COLORS[4] },
        { key: "imu.zgyro", label: "Gyro Z", history: "imu", field: "zgyro", unit: "", color: WAVEFORM_COLORS[0] },
      ],
    },
    {
      id: "mag",
      label: "磁力计",
      channels: [
        { key: "imu.xmag", label: "Mag X", history: "imu", field: "xmag", unit: "", color: WAVEFORM_COLORS[0] },
        { key: "imu.ymag", label: "Mag Y", history: "imu", field: "ymag", unit: "", color: WAVEFORM_COLORS[1] },
        { key: "imu.zmag", label: "Mag Z", history: "imu", field: "zmag", unit: "", color: WAVEFORM_COLORS[2] },
      ],
    },
    {
      id: "rc",
      label: "遥控器",
      channels: [1, 2, 3, 4, 5, 6].map((index, idx) => ({
        key: `rc.ch${index}`,
        label: `CH${index}`,
        history: "rc",
        field: `ch${index}`,
        unit: "",
        color: WAVEFORM_COLORS[idx % WAVEFORM_COLORS.length],
      })),
    },
    {
      id: "servo",
      label: "舵机输出",
      channels: [1, 2, 3, 4, 5, 6].map((index, idx) => ({
        key: `servo.out${index}`,
        label: `OUT${index}`,
        history: "servo",
        field: `out${index}`,
        unit: "",
        color: WAVEFORM_COLORS[(idx + 3) % WAVEFORM_COLORS.length],
      })),
    },
  ];
}

function selectedWaveformChannels() {
  return vehicleWaveformGroups()
    .flatMap((group) => group.channels)
    .filter((channel) => selectedVehicleWaveformKeys.has(channel.key));
}

function renderVehicleWaveformPanel(force = false) {
  const panel = els.vehicleWaveformPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  if (force || panel.dataset.waveformMounted !== "1") {
    panel.innerHTML = `
      <div class="waveform-layout">
        <aside class="setup-detail-card waveform-source-card">
          <div class="waveform-source-head">
            <strong>数据源</strong>
            <button type="button" class="icon-button" data-wave-clear title="清空当前显示">↻</button>
          </div>
          <div id="vehicleWaveformSources" class="waveform-source-list"></div>
        </aside>
        <section class="setup-detail-card waveform-chart-card">
          <div class="waveform-toolbar">
            <div class="segmented-row" id="vehicleWaveWindowControls">
              ${[5, 10, 30, 60].map((sec) => `<button type="button" data-wave-window="${sec}">${sec}s</button>`).join("")}
            </div>
            <button type="button" data-wave-pause title="暂停/继续">Ⅱ</button>
            <div class="segmented-row" id="vehicleWaveRateControls">
              ${[20, 50, 100].map((rate) => `<button type="button" data-wave-rate="${rate}">${rate}Hz</button>`).join("")}
              <span>仅为图表采样率</span>
            </div>
            <div id="vehicleWaveformStats" class="waveform-stats"></div>
          </div>
          <canvas id="vehicleWaveformCanvas" class="waveform-canvas"></canvas>
          <div id="vehicleWaveformLegend" class="waveform-legend"></div>
        </section>
      </div>
      ${dataSourceRibbon(["ATTITUDE", "LOCAL_POSITION_NED", "HIGHRES_IMU/SCALED_IMU", "BATTERY_STATUS", "RC_CHANNELS", "SERVO_OUTPUT_RAW"])}
    `;
    panel.dataset.waveformMounted = "1";
  }
  updateVehicleWaveformChrome();
  requestAnimationFrame(drawVehicleWaveformChart);
}

function updateVehicleWaveformChrome() {
  const panel = els.vehicleWaveformPanel;
  if (!panel) return;
  const sourceList = document.getElementById("vehicleWaveformSources");
  if (sourceList) {
    sourceList.innerHTML = vehicleWaveformGroups().map((group, groupIndex) => `
      <details class="waveform-group" ${groupIndex < 4 ? "open" : ""}>
        <summary>${escapeHtml(group.label)}</summary>
        ${group.channels.map((channel) => `
          <label class="waveform-channel">
            <input type="checkbox" data-wave-key="${escapeHtml(channel.key)}" ${selectedVehicleWaveformKeys.has(channel.key) ? "checked" : ""}>
            <i style="background:${escapeHtml(channel.color)}"></i>
            <span>${escapeHtml(channel.label)}</span>
            <strong>${escapeHtml(latestWaveformValue(channel))}</strong>
          </label>
        `).join("")}
      </details>
    `).join("");
  }
  panel.querySelectorAll("[data-wave-window]").forEach((button) => {
    button.classList.toggle("active", Number(button.dataset.waveWindow) === activeWaveformWindowSec);
  });
  panel.querySelectorAll("[data-wave-rate]").forEach((button) => {
    button.classList.toggle("active", Number(button.dataset.waveRate) === activeWaveformSampleHz);
  });
  const pause = panel.querySelector("[data-wave-pause]");
  if (pause) pause.textContent = vehicleWaveformPaused ? "▶" : "Ⅱ";
}

function waveformHistory() {
  return vehicleWaveformPaused && vehicleWaveformFrozenHistory ? vehicleWaveformFrozenHistory : (setupSnapshot().history || {});
}

function latestWaveformValue(channel) {
  const entries = waveformHistory()[channel.history] || [];
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const value = entries[index]?.[channel.field];
    if (value !== null && value !== undefined && Number.isFinite(Number(value))) {
      return `${Number(value).toFixed(Math.abs(Number(value)) >= 100 ? 0 : 2)}${channel.unit || ""}`;
    }
  }
  return "--";
}

function drawVehicleWaveformChart() {
  const canvas = document.getElementById("vehicleWaveformCanvas");
  if (!canvas) return;
  const channels = selectedWaveformChannels();
  const history = waveformHistory();
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(680, Math.floor(rect.width || 900));
  const height = Math.max(360, Math.floor(rect.height || 520));
  const dpr = window.devicePixelRatio || 1;
  const targetWidth = Math.floor(width * dpr);
  const targetHeight = Math.floor(height * dpr);
  if (canvas.width !== targetWidth || canvas.height !== targetHeight) {
    canvas.width = targetWidth;
    canvas.height = targetHeight;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#080a10";
  ctx.fillRect(0, 0, width, height);
  const plot = { x: 58, y: 20, w: width - 82, h: height - 54 };
  const lastSec = Math.max(0, ...channels.flatMap((channel) => (history[channel.history] || []).map((point) => Number(point.sec || 0))));
  const startSec = Math.max(0, lastSec - activeWaveformWindowSec);
  const series = channels.map((channel) => {
    const raw = Array.isArray(history[channel.history]) ? history[channel.history] : [];
    const points = raw
      .filter((point) => Number(point.sec || 0) >= startSec)
      .map((point) => ({ x: Number(point.sec || 0) - lastSec, y: Number(point[channel.field]) }))
      .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
    return { channel, points: decimateChartSeries(points, activeWaveformSampleHz * activeWaveformWindowSec) };
  });
  const values = series.flatMap((item) => item.points.map((point) => point.y));
  let [minY, maxY] = robustChartRange(values, [-1, 1]);
  const padY = Math.max(0.5, (maxY - minY) * 0.1);
  minY -= padY;
  maxY += padY;
  const xFor = (x) => plot.x + ((x + activeWaveformWindowSec) / activeWaveformWindowSec) * plot.w;
  const yFor = (y) => plot.y + plot.h - ((y - minY) / (maxY - minY)) * plot.h;

  ctx.strokeStyle = "rgba(255,255,255,0.07)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 10; i += 1) {
    const x = plot.x + (plot.w * i) / 10;
    ctx.beginPath();
    ctx.moveTo(x, plot.y);
    ctx.lineTo(x, plot.y + plot.h);
    ctx.stroke();
  }
  for (let i = 0; i <= 5; i += 1) {
    const y = plot.y + (plot.h * i) / 5;
    ctx.beginPath();
    ctx.moveTo(plot.x, y);
    ctx.lineTo(plot.x + plot.w, y);
    ctx.stroke();
  }
  ctx.strokeStyle = "rgba(210,218,235,0.2)";
  ctx.strokeRect(plot.x, plot.y, plot.w, plot.h);
  ctx.save();
  ctx.beginPath();
  ctx.rect(plot.x, plot.y, plot.w, plot.h);
  ctx.clip();
  series.forEach(({ channel, points }) => {
    if (!points.length) return;
    ctx.strokeStyle = channel.color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    points.forEach((point, index) => {
      const x = xFor(point.x);
      const y = yFor(point.y);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
  ctx.restore();

  ctx.fillStyle = "rgba(210,218,235,0.62)";
  ctx.font = "12px Inter, Segoe UI, sans-serif";
  ctx.textAlign = "right";
  ctx.fillText(maxY.toFixed(1), plot.x - 8, plot.y + 4);
  ctx.fillText(minY.toFixed(1), plot.x - 8, plot.y + plot.h);
  ctx.textAlign = "center";
  for (let i = 0; i <= 5; i += 1) {
    const seconds = -activeWaveformWindowSec + (activeWaveformWindowSec * i) / 5;
    ctx.fillText(`${seconds.toFixed(0)}s`, plot.x + (plot.w * i) / 5, height - 14);
  }
  if (!channels.length || !series.some((item) => item.points.length)) {
    ctx.fillStyle = "rgba(210,218,235,0.72)";
    ctx.fillText(channels.length ? "等待所选 MAVLink 数据..." : "在左侧选择要显示的通道", plot.x + plot.w / 2, plot.y + plot.h / 2);
  }
  const stats = document.getElementById("vehicleWaveformStats");
  if (stats) {
    const samples = series.reduce((sum, item) => sum + item.points.length, 0);
    stats.textContent = `${channels.length} 通道 · ${samples} 采样`;
  }
  const legend = document.getElementById("vehicleWaveformLegend");
  if (legend) {
    legend.innerHTML = channels.map((channel) => `<span><i style="background:${escapeHtml(channel.color)}"></i>${escapeHtml(channel.label)}</span>`).join("");
  }
}

// Refresh state, but always render something even on failure
async function initialRefresh() {
  try {
    latestState = applyCachedSessionHistory(await api("/api/state"));
    render(latestState);
    await loadCurrentSessionHistory();
  } catch (_) {
    // Keep default render from above
  }
}
