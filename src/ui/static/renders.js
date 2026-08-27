/* // 渲染函数组：render(state) 总入口、HUD/遥测、计划/任务/事件、布局与 AirSim 模板 */

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

// 多机航线完成监控：本轮派发的所有机都到达各自终点并悬停后提示一次，
// 并复位任务执行状态（missionExecutionActive）。
let activeFlightTaskVehicles = [];

// 返航完成监控：派发返航后，等所有目标机落地锁定再提示
let activeReturnHomeVehicles = [];

function checkReturnHomeCompletion(toolRuntime = {}) {
  if (!activeReturnHomeVehicles.length) return;
  const vehicles = Array.isArray(toolRuntime.vehicles) ? toolRuntime.vehicles : [];
  if (!vehicles.length) return;
  const byName = new Map(vehicles.map((v) => [String(v.vehicle_name || ""), v]));
  const states = activeReturnHomeVehicles.map((n) => byName.get(n));
  if (states.some((s) => s === undefined)) return;
  if (!states.every((v) => !v.flying && !v.armed)) return;
  activeReturnHomeVehicles = [];
  showNotice("✅ 返航完成：已全部到达初始点并降落锁定", "success");
}

function checkFlightTaskCompletion(toolRuntime = {}) {
  if (!activeFlightTaskVehicles.length) return;
  const tasks = toolRuntime.flight_tasks || {};
  const states = activeFlightTaskVehicles.map((name) => tasks[name]);
  // 派发瞬间遥测里还没有对应任务数据，先不判定
  if (states.some((s) => !s)) return;
  if (!states.every((s) => s.state === "done")) return;
  const names = activeFlightTaskVehicles.join("、");
  activeFlightTaskVehicles = [];
  markMissionEdited();
  showNotice(`✅ 航线飞行结束：${names} 已到达各自终点并悬停`, "success");
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

function render(state) {
  const run = state.current_run;
  const toolRuntime = state.tool_runtime || {};
  const drone = toolRuntime.drone || {};
  const supervisor = state.supervisor || {};
  const llm = state.llm || {};

  renderTopbar(run, toolRuntime, supervisor, llm);
  renderOperationContract(toolRuntime);
  renderContextUsage(state.memory || {});
  renderTelemetry(drone, toolRuntime);
  renderPlan(run);
  renderTaskRuns(state.task_runs || state.memory?.task_runs || {});
  renderApprovalDialog(run, state.pending_approvals || []);
  renderEvents(state.events || []);
  renderTools(state.tools || [], toolRuntime.tool_cards || [], toolRuntime);
  renderMemory(state.memory || {});
  renderSkills();
  renderChat(state.messages || [], run, llm);
  renderWaypoints();
  updateMapView(state);
  renderSessions(state.sessions || [], state.current_session);
  renderCurrentSessionLabel(state.current_session);
  if (els.systemSettingsModal && !els.systemSettingsModal.hidden) updateVehicleSettingsAvailability();
  syncCommandSubmitState();
  syncHeader();
}

function renderOperationContract(toolRuntime) {
  const contract = toolRuntime?.operation_contract || {};
  const linked = Boolean(toolRuntime?.connected && !toolRuntime?.stale_connection);
  const commandChannel = String(contract.command_channel || "");
  const missionChannel = String(contract.mission_channel || "");
  if (els.operationChannel) {
    els.operationChannel.textContent = linked
      ? `${commandChannel} · 航点: ${missionChannel}`
      : `${commandChannel || "无控制通道"} · 未连接`;
    els.operationChannel.title = [
      `控制: ${commandChannel || "--"}`,
      `任务: ${missionChannel || "--"}`,
      `返航: ${contract.return_channel || "--"}`,
      `位置: ${contract.position_source || "--"}`,
    ].join("\n");
  }
  document.querySelectorAll("[data-waypoint-action='deploy_start']").forEach((button) => {
    const needsGlobalGps = contract.backend === "px4_mavlink"
      && Boolean(applicationSettings.safety.require_gps_for_global_mission);
    button.disabled = !linked || (needsGlobalGps && !contract.global_mission_ready);
    button.title = button.disabled && needsGlobalGps
      ? "真实 PX4 需要可靠 GPS 后才能上传并开始全局航点"
      : `通过 ${missionChannel || "当前后端"} 上传并开始航线`;
  });
}

function renderContextUsage(memory) {
  if (!els.contextUsage) return;
  const usage = memory?.conversation || {};
  const percent = Number(usage.context_percent);
  const used = Number(usage.estimated_context_tokens);
  const total = Number(usage.context_window);
  const visible = applicationSettings.agent.show_context_usage !== false;
  els.contextUsage.hidden = !visible;
  if (!visible) return;
  els.contextUsage.textContent = Number.isFinite(percent) ? `CTX ${percent.toFixed(percent >= 10 ? 0 : 1)}%` : "CTX --";
  els.contextUsage.title = Number.isFinite(used) && Number.isFinite(total)
    ? `本次模型上下文估算 ${used.toLocaleString()} / ${total.toLocaleString()} tokens；完整会话仍会保存`
    : "等待模型上下文统计";
  els.contextUsage.classList.toggle("warn", percent >= 70 && percent < 90);
  els.contextUsage.classList.toggle("danger", percent >= 90);
}

function renderApprovalDialog(run, pendingApprovals) {
  const el = document.getElementById("approvalDialog");
  if (!el) return;
  const pending = (pendingApprovals && pendingApprovals.length > 0)
    ? pendingApprovals
    : (run && run.status === "awaiting_approval" ? [{
        run_id: run.run_id,
        command: run.command,
        tool: (run.plan && run.plan.steps && run.plan.steps[0] && run.plan.steps[0].tool) || "",
        risk_level: run.risk_level || "high",
        reason: (run.plan && run.plan.risk_notes && run.plan.risk_notes.join("; ")) || "high-risk operation",
        status: "pending",
      }] : []);
  if (!pending.length) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  el.classList.remove("hidden");
  el.innerHTML = pending.map((a) => `
    <div class="approval-card" data-run-id="${escapeHtml(a.run_id)}">
      <div class="approval-header">
        ${riskBadge(a.risk_level)}
        <strong>等待操作员确认</strong>
      </div>
      <div class="approval-body">
        <div><span>命令:</span> ${escapeHtml(a.command || "")}</div>
        <div><span>工具:</span> <code>${escapeHtml(a.tool || "")}</code></div>
        <div><span>原因:</span> ${escapeHtml(a.reason || "")}</div>
      </div>
      <div class="approval-actions">
        <button class="btn-approve" data-approve-run="${escapeHtml(a.run_id)}">确认执行</button>
        <button class="btn-reject" data-reject-run="${escapeHtml(a.run_id)}">拒绝</button>
      </div>
    </div>
  `).join("");
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

function renderSessions(sessions, currentSession) {
  if (!els.sessionsList) return;
  const currentId = currentSession?.id || "";
  if (!sessions.length) {
    els.sessionsList.innerHTML = `<div class="empty">暂无会话</div>`;
    return;
  }
  els.sessionsList.innerHTML = sessions.map((s) => {
    const isActive = s.id === currentId;
    const timeText = formatSessionTime(s.updated_at || s.created_at);
    return `
      <div class="session-item ${isActive ? "active" : ""}" data-session-id="${escapeHtml(s.id)}" data-session-action="load">
        <div class="session-name">${escapeHtml(s.name || "未命名对话")}</div>
        <div class="session-meta">
          <span>${timeText}</span>
          <span>${s.message_count || 0} 条消息</span>
        </div>
        <div class="session-actions" data-stop-propagation>
          <button data-session-id="${escapeHtml(s.id)}" data-session-action="export" data-session-format="markdown" title="导出完整会话">↓</button>
          <button class="delete-session" data-session-id="${escapeHtml(s.id)}" data-session-action="delete" title="删除">×</button>
        </div>
      </div>
    `;
  }).join("");
}

function renderCurrentSessionLabel(currentSession) {
  if (!els.currentSessionLabel) return;
  els.currentSessionLabel.textContent = currentSession?.name || "";
  els.currentSessionLabel.title = currentSession?.name || "";
}

function startHeaderSessionRename() {
  const currentSession = latestState?.current_session;
  if (!currentSession?.id || !els.currentSessionLabel) return;
  const sessionId = currentSession.id;
  const span = els.currentSessionLabel;
  const currentName = span.textContent;
  span.hidden = true;

  const input = document.createElement("input");
  input.type = "text";
  input.className = "current-session-input";
  input.value = currentName;
  span.parentNode.insertBefore(input, span.nextSibling);
  input.focus();
  input.select();

  const cleanup = () => {
    input.remove();
    span.hidden = false;
  };

  const finish = async (save) => {
    const newName = input.value.trim();
    if (save && newName && newName !== currentName) {
      span.textContent = newName;
      span.title = newName;
      cleanup();
      await renameSession(sessionId, newName);
    } else {
      span.textContent = currentName;
      span.title = currentName;
      cleanup();
    }
  };

  input.addEventListener("blur", () => finish(true));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") input.blur();
    else if (event.key === "Escape") finish(false);
  });
}

function formatSessionTime(ts) {
  if (!ts) return "";
  const date = new Date(typeof ts === "number" && ts < 1e11 ? ts * 1000 : ts);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const diffSec = Math.floor((now - date) / 1000);
  if (diffSec < 60) return "刚刚";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分钟前`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} 小时前`;
  if (diffSec < 604800) return `${Math.floor(diffSec / 86400)} 天前`;
  return date.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

function syncHeader() {
  const listOpen = els.sessionsPanel?.classList.contains("is-open") ?? false;
  if (els.sessionNavBtn) {
    if (listOpen) {
      els.sessionNavBtn.hidden = true;
    } else {
      els.sessionNavBtn.hidden = false;
      els.sessionNavBtn.textContent = "←";
      els.sessionNavBtn.title = "返回会话列表";
    }
  }
  if (els.currentSessionLabel) els.currentSessionLabel.hidden = listOpen;
}

function openSessionsPanel() {
  if (!els.sessionsPanel) return;
  els.sessionsPanel.classList.add("is-open");
  els.sessionsPanel.hidden = false;
  els.agentColumn?.classList.add("sessions-open");
  syncHeader();
}

function closeSessionsPanel() {
  if (!els.sessionsPanel) return;
  els.sessionsPanel.classList.remove("is-open");
  els.sessionsPanel.hidden = true;
  els.agentColumn?.classList.remove("sessions-open");
  syncHeader();
}

function backendDisplayName(toolRuntime = {}) {
  const profile = toolRuntime.backend_profile || {};
  return profile.name || profile.id || toolRuntime.backend || "Vehicle backend";
}

async function createSession() {
  try {
    await post("/api/sessions", { name: "新对话" });
    closeSessionsPanel();
    showNotice("新会话已创建", "success");
  } catch (error) {
    showNotice(error.message || "创建会话失败", "error");
  }
}

async function loadSession(sessionId) {
  try {
    const result = await post(`/api/sessions/${encodeURIComponent(sessionId)}/load`, {});
    const messages = Array.isArray(result?.session?.messages) ? result.session.messages : [];
    fullSessionMessageCache.set(sessionId, messages);
    if (latestState) {
      latestState.current_session = result.session;
      latestState.messages = messages;
      render(latestState);
    }
    closeSessionsPanel();
    showNotice("会话已切换", "info");
  } catch (error) {
    showNotice(error.message || "切换会话失败", "error");
  }
}

function mergeSessionMessages(completeMessages, recentMessages) {
  const byId = new Map();
  [...(completeMessages || []), ...(recentMessages || [])].forEach((message) => {
    if (message?.id) byId.set(message.id, message);
  });
  return [...byId.values()].sort((left, right) => (
    Number(left.created_at || 0) - Number(right.created_at || 0)
  ));
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

async function renameSession(sessionId, name) {
  try {
    await post(`/api/sessions/${encodeURIComponent(sessionId)}/rename`, { name });
  } catch (error) {
    showNotice(error.message || "重命名失败", "error");
  }
}

async function deleteSession(sessionId) {
  try {
    await post(`/api/sessions/${encodeURIComponent(sessionId)}/delete`, {});
    showNotice("会话已删除", "info");
  } catch (error) {
    showNotice(error.message || "删除会话失败", "error");
  }
}

function updateFlightControlButtons(toolRuntime = {}) {
  const linked = Boolean(toolRuntime.connected) && !toolRuntime.stale_connection;
  const heartbeatAge = Number(toolRuntime.drone?.heartbeat_age_s);
  const linkReason = Number.isFinite(heartbeatAge)
    ? `飞控心跳已过期（${heartbeatAge.toFixed(1)}s），请检查连接设置`
    : "飞控未连接，请检查连接设置";
  const contract = toolRuntime.operation_contract || {};
  document.querySelectorAll(".map-toolbar button").forEach((button) => {
    if (!button.dataset.baseTitle) button.dataset.baseTitle = button.title || "飞行控制";
    const tool = button.dataset.tool || "";
    const control = button.dataset.control || "";
    const needsLink = Boolean(tool) || ["hover", "land", "return_home", "rtl"].includes(control);
    if (!needsLink) return;
    // busy 瞬时值会让按钮闪烁禁用，指令本身由执行器排队，不再据此禁用
    const disabled = !linked;
    button.disabled = disabled;
    const channel = control === "return_home"
      ? contract.return_channel
      : contract.command_channel;
    button.title = disabled
      ? (!linked ? linkReason : "飞控正在执行任务")
      : `${button.dataset.baseTitle}${channel ? ` · ${channel}` : ""} · 目标: ${controlTargetLabel()}`;
  });
}

function renderTopbar(run, toolRuntime, supervisor, llm) {
  const connected = Boolean(toolRuntime.connected) && !toolRuntime.stale_connection;
  const backendName = backendDisplayName(toolRuntime);
  const backendState = connected ? "ONLINE" : "OFFLINE";
  if (els.connectionText) {
    els.connectionText.textContent = `${backendName} ${backendState}`;
    els.connectionText.title = `${backendName} ${backendState}`;
  }
  if (els.connectionDot) {
    els.connectionDot.classList.toggle("connected", connected);
  }
  const routeBadge = run?.route_strategy ? ` · ${run.route_strategy}` : "";
  if (els.plannerBadge) els.plannerBadge.textContent = `${run?.plan?.planner_source || "planner"}${routeBadge}`;
  updateFlightControlButtons(toolRuntime);
}

function renderMissionMetrics(drone) {
  drone = drone || latestState?.tool_runtime?.drone || {};
  const pos = drone.position_ned || { x: 0, y: 0, z: 0 };
  const altitude = Math.max(0, Math.abs(Number(pos.z || 0)));
  const toolRuntime = latestState?.tool_runtime || {};
  const connected = Boolean(toolRuntime.connected) && !toolRuntime.stale_connection;

  const dronePos = getDroneLatLon(drone);
  const mapPositionReliable = Boolean(dronePos);
  const horizontalDistM = Math.hypot(Number(pos.x || 0), Number(pos.y || 0));

  // 当前目标航点：按无人机在航线上的投影推断，避免离开 1 号航点后虚线/指标倒回旧航点
  let currentWpIdx = -1;
  if (dronePos?.lat != null && dronePos?.lon != null) {
    const target = currentTargetWaypoint([dronePos.lon, dronePos.lat]);
    currentWpIdx = target ? missionWaypoints.indexOf(target) : -1;
  }

  const prevIdx = currentWpIdx > 0 ? currentWpIdx - 1 : -1;
  const nextIdx = currentWpIdx >= 0 && currentWpIdx < missionWaypoints.length - 1 ? currentWpIdx + 1 : -1;

  const targetAlt = nextIdx >= 0
    ? (missionWaypoints[nextIdx].alt_m || 0)
    : currentWpIdx >= 0
    ? (missionWaypoints[currentWpIdx].alt_m || 0)
    : 0;
  const altDiff = targetAlt - altitude;

  // BRG：当前航点指向下一个航点的方位（任务航向）
  const bearing = nextIdx >= 0
    ? calculateBearing(missionWaypoints[currentWpIdx].lat, missionWaypoints[currentWpIdx].lon, missionWaypoints[nextIdx].lat, missionWaypoints[nextIdx].lon)
    : (currentWpIdx > 0
      ? calculateBearing(missionWaypoints[prevIdx].lat, missionWaypoints[prevIdx].lon, missionWaypoints[currentWpIdx].lat, missionWaypoints[currentWpIdx].lon)
      : null);

  // DIST PREV：当前航点与上一航点之间的航线段距离
  const distPrev = currentWpIdx > 0
    ? haversineMeters(missionWaypoints[prevIdx].lat, missionWaypoints[prevIdx].lon, missionWaypoints[currentWpIdx].lat, missionWaypoints[currentWpIdx].lon)
    : null;

  // HOME DIST：当前位置到飞控 HOME_POSITION 的距离。航点距离不会再伪装成飞行范围。
  const home = mapPositionReliable ? returnHomeGeo(drone, toolRuntime) : null;
  const homeDistance = home?.lat != null && home?.lon != null
    ? haversineMeters(dronePos.lat, dronePos.lon, home.lat, home.lon)
    : (!isRealVehicleRuntime(toolRuntime) ? horizontalDistM : null);

  // TILT：机体倾斜角（roll/pitch 合成，后端均为弧度）
  const att = drone.attitude_rad || {};
  const tiltDeg = Math.sqrt(Math.pow(Number(att.roll || 0), 2) + Math.pow(Number(att.pitch || 0), 2)) * (180 / Math.PI);

  if (els.metricHeading) els.metricHeading.textContent = `${fmt(drone.heading_deg || 0)}°`;
  if (els.metricAltDiff) els.metricAltDiff.textContent = missionWaypoints.length && hasReliableVehicleMapPosition(drone, toolRuntime) ? formatSigned(altDiff, "m") : "--";
  if (els.metricBearing) els.metricBearing.textContent = bearing != null ? `${fmt(bearing)}°` : "--";
  if (els.metricDistPrev) els.metricDistPrev.textContent = distPrev != null ? formatDistance(distPrev) : "--";
  if (els.metricMaxRange) els.metricMaxRange.textContent = connected && homeDistance != null ? formatDistance(homeDistance) : "--";
  if (els.metricTilt) els.metricTilt.textContent = `${fmt(tiltDeg)}°`;
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



function renderToolCall(step) {
  const state = step.status || "pending";
  const mark = state === "completed" || state === "planned"
    ? "✓"
    : state === "failed" || state === "blocked"
      ? "!"
      : "•";
  const tool = step.tool || "tool";
  return `
    <div class="tool-call ${state}">
      <span class="tool-dot">${mark}</span>
      <div>
        <strong>${escapeHtml(humanToolLabel(tool, step.label))}</strong>
        <small>${escapeHtml(humanToolStatus(state))} · ${escapeHtml(tool)}</small>
      </div>
    </div>
  `;
}

function humanToolLabel(tool, label = "") {
  const cleanLabel = String(label || "").trim();
  if (cleanLabel && !/^Direct tool call:/i.test(cleanLabel) && cleanLabel !== tool) {
    return cleanLabel;
  }
  const labels = {
    drone_connect: "连接飞控链路",
    drone_disconnect: "断开飞控链路",
    drone_list_vehicles: "查看可用无人机",
    drone_get_status: "读取无人机状态",
    drone_arm: "解锁电机",
    drone_disarm: "锁定电机",
    drone_takeoff: "起飞",
    drone_land: "降落",
    drone_hover: "悬停",
    drone_fly_to: "飞往目标坐标",
    drone_move_relative: "按相对方向移动",
    drone_fly_velocity: "按速度飞行",
    drone_fly_path: "沿航线飞行",
    drone_upload_mission: "上传飞行任务",
    drone_download_mission: "下载飞行任务",
    drone_clear_mission: "清空飞行任务",
    drone_start_mission: "启动飞行任务",
    drone_get_mission_progress: "读取任务进度",
    drone_rotate_to: "调整朝向",
    drone_set_mode: "切换飞行模式",
    airsim_take_photo: "拍摄图像",
    airsim_vlm_analyze_image: "分析摄像头画面",
    airsim_vlm_confirm_target: "确认画面目标",
    airsim_get_sensors: "读取传感器",
    airsim_get_depth_map: "读取深度图",
    airsim_detect_objects: "识别画面目标",
    airsim_search_target: "搜索目标",
    airsim_approach_target: "接近目标",
    airsim_track_object: "跟踪目标",
    airsim_task_status: "读取后台任务状态",
    airsim_task_cancel: "取消后台任务",
    airsim_check_obstacle: "检查障碍物",
    memory_store: "记录任务记忆",
  };
  return labels[tool] || String(tool || "tool").replaceAll("_", " ");
}

function humanPlanSummary(plan, fallback = "") {
  const summary = String(plan?.summary || fallback || "").trim();
  const directMatch = summary.match(/^L0 direct route:\s*([\w_]+)/i);
  if (directMatch) return humanToolLabel(directMatch[1]);
  return summary || "任务处理中";
}

function visiblePlanReasoning(reasoning) {
  const text = String(reasoning || "").trim();
  if (!text) return "";
  if (/TaskRouter selected|no LLM planning|direct tool call/i.test(text)) return "";
  return text;
}

function humanToolStatus(status) {
  if (status === "completed") return "已完成";
  if (status === "failed") return "失败";
  if (status === "blocked") return "已阻止";
  if (status === "running") return "执行中";
  if (status === "planned") return "已规划";
  if (status === "awaiting_approval") return "等待确认";
  if (status === "cancelled") return "已取消";
  if (status === "paused") return "已暂停";
  return "等待中";
}

function riskBadge(riskLevel) {
  if (riskLevel === "high") return `<span class="risk-badge risk-high">高风险</span>`;
  if (riskLevel === "elevated") return `<span class="risk-badge risk-elevated">中风险</span>`;
  return "";
}

function renderPlan(run) {
  if (!run || !run.plan) {
    if (els.planSummary) els.planSummary.innerHTML = `<div class="empty">等待任务指令</div>`;
    if (els.runProgress) els.runProgress.style.width = "0%";
    return;
  }

  const planner = run.plan.planner_source || "planner";
  const model = run.plan.planner_model ? ` · ${run.plan.planner_model}` : "";
  const route = [run.task_level, run.route_strategy].filter(Boolean).join(" / ");
  const reasoningText = visiblePlanReasoning(run.plan.reasoning);
  const reasoning = reasoningText ? `<p>${escapeHtml(reasoningText)}</p>` : "";
  const risks = (run.plan.risk_notes || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("");
  const badge = riskBadge(run.risk_level);

  if (els.planSummary) els.planSummary.innerHTML = `
    <strong>${escapeHtml(humanPlanSummary(run.plan, run.summary))} ${badge}</strong>
    <em>${escapeHtml([planner + model, route].filter(Boolean).join(" · "))}</em>
    ${reasoning}
    ${risks ? `<div class="risk-notes">${risks}</div>` : ""}
  `;
  if (els.runProgress) els.runProgress.style.width = `${Math.max(0, Math.min(100, run.progress || 0))}%`;
}

function humanRunStatus(status) {
  const labels = {
    completed: "完成",
    planned: "已规划",
    failed: "失败",
    blocked: "阻断",
    cancelled: "取消",
    running: "执行中",
    queued: "排队",
    responding: "汇总中",
    awaiting_approval: "待确认",
    paused: "暂停",
  };
  return labels[status] || status || "未知";
}

function renderTaskRuns(taskRuns) {
  if (!els.taskRunList) return;
  const runs = Array.isArray(taskRuns?.recent) ? taskRuns.recent : [];
  if (!runs.length) {
    els.taskRunList.innerHTML = `<div class="empty">暂无可复盘任务</div>`;
    return;
  }
  els.taskRunList.innerHTML = runs.slice(0, 8).map((run) => {
    const counters = run.counters || {};
    const title = run.summary || run.command || run.intent || "任务记录";
    const status = humanRunStatus(run.status);
    const time = run.started_at ? new Date(run.started_at * 1000).toLocaleString() : "";
    const meta = [
      status,
      run.route_strategy || run.task_level || "",
      `${counters.steps_ok || 0}/${counters.steps_total || 0} 步`,
      `${counters.events || 0} 事件`,
    ].filter(Boolean).join(" · ");
    const fail = run.failure_reason ? `<p class="task-run-failure">${escapeHtml(run.failure_reason)}</p>` : "";
    return `
      <article class="compact-item task-run-item ${escapeHtml(run.status || "")}" title="${escapeHtml(run.run_id || "")}">
        <div class="memory-item-head">
          <strong>${escapeHtml(title)}</strong>
          <small>${escapeHtml(status)}</small>
        </div>
        <p>${escapeHtml(meta)}</p>
        ${time ? `<span class="task-run-time">${escapeHtml(time)}</span>` : ""}
        ${fail}
      </article>
    `;
  }).join("");
}

function renderEvents(events) {
  const ordered = [...events].slice(-30).reverse();
  if (!ordered.length) {
    els.eventList.innerHTML = `<div class="empty">暂无事件</div>`;
    return;
  }
  els.eventList.innerHTML = ordered.map((event) => {
    const time = new Date((event.timestamp || 0) * 1000).toLocaleTimeString();
    return `
      <article class="event-item ${event.level}">
        <div class="event-line">
          <strong>${escapeHtml(event.source || "system")}</strong>
          <span>${time}</span>
        </div>
        <p class="event-message">${escapeHtml(event.message || "")}</p>
      </article>
    `;
  }).join("");
}







const WAYPOINT_TYPE_LABELS = {
  waypoint: "航点",
  takeoff: "起飞",
  land: "降落",
  rtl: "返航",
};

function renderWaypoints() {
  syncWaypointActionLabels();
  renderMissionMetrics();

  if (!els.waypointList) return;

  if (!missionWaypoints.length) {
    const hint = isMultiVehiclePlanning() && !currentMissionVehicleName()
      ? `点击左上角的无人机选择目标机<br>再点击地图添加航点`
      : `点击地图添加航点<br>双击航点可删除`;
    els.waypointList.innerHTML = `<div class="empty">${hint}</div>`;
    hideWaypointProperties();
    return;
  }

  els.waypointList.innerHTML = missionWaypoints.map((wp, index) => {
    const selected = index === selectedWaypointIndex ? "selected" : "";
    const typeLabel = WAYPOINT_TYPE_LABELS[wp.type] || "航点";
    return `
      <article class="waypoint-item ${selected}" data-waypoint-row="${index}">
        <span class="waypoint-index">${index + 1}</span>
        <div class="waypoint-main">
          <strong>${typeLabel}</strong>
        </div>
        <button class="delete-waypoint" data-waypoint-delete="${index}" title="删除此航点">×</button>
      </article>
    `;
  }).join("");

  document.querySelectorAll("[data-waypoint-delete]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const idx = Number(button.dataset.waypointDelete);
      missionWaypoints.splice(idx, 1);
      markMissionEdited();
      if (selectedWaypointIndex === idx) selectedWaypointIndex = -1;
      renderWaypoints();
      drawMissionPath();
    }, { once: true });
  });

  document.querySelectorAll("[data-waypoint-row]").forEach((row) => {
    row.addEventListener("click", () => {
      const idx = Number(row.dataset.waypointRow);
      selectedWaypointIndex = idx;
      renderWaypoints();
      drawMissionPath();
      const wp = missionWaypoints[idx];
      if (wp && maplibreMap) maplibreMap.panTo([wp.lon, wp.lat], { animate: true });
      showWaypointProperties(idx);
    });
  });

}

function hideWaypointProperties() {
  if (els.waypointProperties) els.waypointProperties.classList.add("hidden");
}

function showWaypointProperties(index) {
  const wp = missionWaypoints[index];
  if (!wp || !els.waypointProperties) return;
  selectedWaypointIndex = index;
  els.waypointProperties.classList.remove("hidden");
  if (els.wpPropType) els.wpPropType.value = wp.type || "waypoint";
  if (els.wpPropAlt) els.wpPropAlt.value = wp.alt_m ?? 3;
  if (els.wpPropSpeed) els.wpPropSpeed.value = wp.speed_mps ?? 2;
  if (els.wpPropHold) els.wpPropHold.value = wp.hold_s ?? 0;
  if (els.wpPropAccept) els.wpPropAccept.value = wp.acceptance_radius_m ?? 2;
}

function applyWaypointProperties() {
  if (selectedWaypointIndex < 0 || selectedWaypointIndex >= missionWaypoints.length) return;
  const wp = missionWaypoints[selectedWaypointIndex];
  wp.type = els.wpPropType ? els.wpPropType.value : wp.type;
  wp.alt_m = Math.max(0, parseFloat(els.wpPropAlt?.value) || 0);
  wp.speed_mps = Math.max(0.1, parseFloat(els.wpPropSpeed?.value) || 2);
  wp.hold_s = Math.max(0, parseFloat(els.wpPropHold?.value) || 0);
  wp.acceptance_radius_m = Math.max(0.5, parseFloat(els.wpPropAccept?.value) || 2);
  markMissionEdited();
  renderWaypoints();
  drawMissionPath();
}

function buildWaypointCommand() {
  const route = missionWaypoints
    .map((wp, index) => `${index + 1}. lat ${wp.lat}, lon ${wp.lon}, alt ${wp.alt_m}m`)
    .join("；");
  return `按以下航点规划并执行飞行，速度2m/s，完成后悬停并报告状态：${route}`;
}

function updateMapView(state) {
  if (!maplibreMap) return;

  const runtime = state.tool_runtime || {};
  const drone = runtime.drone || {};
  const backendName = backendDisplayName(runtime);
  const linked = Boolean(runtime.connected) && !runtime.stale_connection;
  // function-scoped: used both inside the multi-vehicle branch and at the
  // status line below — a block-scoped declaration would throw
  // "gps is not defined" (ReferenceError) inside the else branch consumers
  const gps = droneGpsPosition(drone, runtime);

  const vehicles = Array.isArray(runtime.vehicles) ? runtime.vehicles : [];
  if (vehicles.length > 1) {
    // 多机模式：每机 marker + 独立轨迹（单机逻辑保留给 vehicles.length <= 1）
    stopDroneAnimation();
    if (droneMarker) {
      droneMarker.remove();
      droneMarker = null;
    }
    droneRenderedLngLat = null;
    droneLastTelemetryLngLat = null;
    updateVehicleMarkers(vehicles, runtime);
    if (!applicationSettings.map.show_vehicle_track) clearVehicleTrack();
    // 首次定位跟随面板选中的目标机（未选则默认第一架）
    const followTargetName = currentMissionVehicleName();
    const followTarget = vehicles.find((v) => String(v.vehicle_name || "") === followTargetName) || vehicles[0];
    const firstPos = vehicleMarkerPosition(followTarget, runtime);
    if (firstPos && linked && applicationSettings.map.follow_vehicle && !mapCenteredOnFirstVehicle && !maplibreMap._userPanned) {
      mapCenteredOnFirstVehicle = true;
      maplibreMap.jumpTo({ center: firstPos });
    }
  } else {
  // 更新无人机位置 marker
  if (gps) {
    // gps 为 [lat, lon]，MapLibre 用 [lng, lat]
    const lngLat = [gps[1], gps[0]];
    const displayJumpM = droneLastTelemetryLngLat
      ? haversineMeters(droneLastTelemetryLngLat[1], droneLastTelemetryLngLat[0], lngLat[1], lngLat[0])
      : 0;
    const maxDisplayJumpM = Number(applicationSettings.safety.max_display_jump_m || 120);
    if (isRealVehicleRuntime(runtime) && displayJumpM > maxDisplayJumpM && !drone.flying) {
      els.mapStatus.textContent = `GPS 显示跳变 ${formatDistance(displayJumpM)}，地面状态下已拒绝移动地图标记`;
      return;
    }
    const targetWaypoint = currentTargetWaypoint(lngLat);
    const heading = resolveDroneHeading(drone, lngLat);
    const streamFreshAt = Number(runtime.telemetry_stream_received_at_ms || 0);
    const streamFresh = Boolean(runtime.telemetry_stream_active) && (!streamFreshAt || Date.now() - streamFreshAt < 1500);
    const immediateMarkerUpdate = rosTelemetryConnected || streamFresh;
    updateDroneMarker(lngLat, heading, { immediate: immediateMarkerUpdate });
    droneLastTelemetryLngLat = lngLat.slice();
    if (applicationSettings.map.show_vehicle_track) updateVehicleTrack(lngLat, drone, linked);
    else clearVehicleTrack();
    if (!SHOW_ACTIVE_LEG) clearActiveLeg();
    // 首次或长时间未连接后定位到无人机
    if (linked && applicationSettings.map.follow_vehicle && !mapCenteredOnFirstVehicle && !maplibreMap._userPanned) {
      mapCenteredOnFirstVehicle = true;
      maplibreMap.jumpTo({ center: lngLat });
    }
  } else if (linked && isRealVehicleRuntime(runtime)) {
    stopDroneAnimation();
    if (droneMarker) {
      droneMarker.remove();
      droneMarker = null;
    }
    droneRenderedLngLat = null;
    droneLastTelemetryLngLat = null;
    clearVehicleTrack();
    clearActiveLeg();
  }
  }

  // 更新 home marker（PX4 后端可能有真实 home；AirSim 单机时用模拟原点）
  // 多机模式下每机有自己的 H 标记，中央的旧 homeMarker 会误导坐标对应，直接移除
  const multiVehicleHomeDisplay =
    isMultiVehiclePlanning() ||
    (Array.isArray(state?.tool_runtime?.vehicles) && state.tool_runtime.vehicles.length > 1);
  const homeGps = homeGpsPosition(drone, runtime, state);
  if (multiVehicleHomeDisplay) {
    if (homeMarker) {
      homeMarker.remove();
      homeMarker = null;
    }
  } else if (homeGps && homeMarker) {
    homeMarker.setLngLat([homeGps[1], homeGps[0]]);
  }

  // 航点不随遥测重绘（参考 QGC：航点 visual 独立，只在 plan 变化时更新，避免闪烁）
  // drawMissionPath 由航点增删/选中/拖拽时显式调用

  const wpCount = missionWaypoints.length;
  els.canvasScale.textContent = `${wpCount} WP · ${backendName}`;
  const fix = Number(drone.gps_fix_type || 0);
  const sats = Number(drone.satellites_visible);
  const accuracy = Number(drone.gps_horizontal_accuracy_m);
  const gpsMeta = [
    fix ? `Fix ${fix}` : "",
    Number.isFinite(sats) ? `${sats} sats` : "",
    Number.isFinite(accuracy) ? `±${accuracy.toFixed(1)} m` : "",
  ].filter(Boolean).join(" · ");
  els.mapStatus.textContent = linked
    ? (gps ? `${backendName} · ${drone.position_source || "GPS"}${gpsMeta ? ` · ${gpsMeta}` : ""}` : `${backendName} 已连接 · 等待可靠 GPS`)
    : `等待 ${backendName} 链路`;
}

function drawMissionPath() {
  if (!maplibreMap) return;
  const pathSource = maplibreMap.getSource("path-source");
  const wpSource = maplibreMap.getSource("wp-source");
  if (!pathSource || !wpSource) return;

  // 航线统一由 plan-source 按机配色绘制（每机一色，当前目标机高亮），
  // 旧的白色 path-line 不再使用，保持 source 存在以兼容既有 layer 定义。
  pathSource.setData({ type: "FeatureCollection", features: [] });

  const planSource = maplibreMap.getSource("plan-source");
  if (planSource) {
    const features = [];
    const activeTarget = currentMissionVehicleName();
    const addRoute = (vehicle, route) => {
      if (!Array.isArray(route) || !route.length) return;
      const color = vehicleRouteColor(vehicle);
      const isActive = (vehicle || "") === activeTarget;
      if (route.length >= 2) {
        features.push({
          type: "Feature",
          properties: { vehicle, color, active: isActive, kind: "line" },
          geometry: { type: "LineString", coordinates: route.map((wp) => [wp.lon, wp.lat]) },
        });
      }
      for (const wp of route) {
        features.push({
          type: "Feature",
          properties: { vehicle, color, active: isActive, kind: "dot" },
          geometry: { type: "Point", coordinates: [wp.lon, wp.lat] },
        });
      }
    };
    if (!activeTarget) {
      // 单机模式：当前航线即全部
      addRoute("", missionWaypoints);
    } else {
      for (const [vehicle, route] of Object.entries(missionPlans)) {
        if (vehicle === activeTarget) continue;
        addRoute(vehicle, route);
      }
      addRoute(activeTarget, missionWaypoints);
    }
    planSource.setData({ type: "FeatureCollection", features });
  }

  // 航点 GeoJSON features（id 用于 setFeatureState，type 用于 sprite 配色）
  const features = missionWaypoints.map((wp, index) => ({
    type: "Feature",
    id: index + 1,
    properties: { seq: index + 1, type: wp.type || "waypoint" },
    geometry: { type: "Point", coordinates: [wp.lon, wp.lat] },
  }));
  wpSource.setData({ type: "FeatureCollection", features });

  // 拖拽中只更新数据，不清除/设置选中态（避免中断拖拽）
  if (wpDragging) return;

  // 选中态：先清除全部，再设置当前选中
  for (let i = 0; i < missionWaypoints.length; i++) {
    try {
      maplibreMap.setFeatureState({ source: "wp-source", id: i + 1 }, { selected: false });
    } catch (err) {
      // 忽略 feature 不存在
    }
  }
  if (selectedWaypointIndex >= 0) {
    try {
      maplibreMap.setFeatureState(
        { source: "wp-source", id: selectedWaypointIndex + 1 },
        { selected: true },
      );
    } catch (err) {
      // 忽略 feature 不存在
    }
  }
  drawMissionProfile();
  refreshActiveLegFromCurrentPosition();
}

function drawFence() {
  if (!maplibreMap) return;
  const fenceSource = maplibreMap.getSource("fence-source");
  if (!fenceSource) return;

  if (missionFence.length < 2) {
    fenceSource.setData({ type: "FeatureCollection", features: [] });
    return;
  }

  const coords = missionFence.map((p) => [p.lon, p.lat]);
  if (missionFence.length >= 3) coords.push(coords[0]);

  fenceSource.setData({
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: {
          type: missionFence.length >= 3 ? "Polygon" : "LineString",
          coordinates: missionFence.length >= 3 ? [coords] : coords,
        },
      },
    ],
  });
}





// NED (north/east/down meters) → GPS (lat/lon degrees) 转换
// 使用平面近似，小范围（<10km）内有效
function currentDroneGeo(drone, runtime = latestState?.tool_runtime || {}) {
  const gps = drone?.gps || {};
  const realVehicle = isRealVehicleRuntime(runtime);
  const gpsLooksValid = Boolean(gps.lat && gps.lon && Math.abs(Number(gps.lat)) > 0.001);
  if (gpsLooksValid && (!realVehicle || hasReliableVehicleMapPosition(drone, runtime))) {
    return {
      lat: Number(gps.lat),
      lon: Number(gps.lon),
      alt_m: Number(gps.alt ?? gps.relative_alt ?? 0),
    };
  }
  if (realVehicle) return null;
  const pos = drone?.position_ned || {};
  if (typeof pos.x === "number" || typeof pos.y === "number") {
    const gpsFromNed = nedToGps(Number(pos.x || 0), Number(pos.y || 0), Number(pos.z || 0));
    return { lat: gpsFromNed.lat, lon: gpsFromNed.lon, alt_m: gpsFromNed.alt };
  }
  return null;
}

function droneGpsPosition(drone, runtime) {
  const geo = currentDroneGeo(drone, runtime);
  return geo ? [geo.lat, geo.lon] : null;
}

function homeGpsPosition(drone, runtime, state = latestState) {
  if (isRealVehicleRuntime(runtime) && !hasReliableVehicleMapPosition(drone, runtime)) {
    return null;
  }
  const home = returnHomeGeo(drone, runtime);
  return home ? [home.lat, home.lon] : null;
}

function returnHomeGeo(drone, runtime = latestState?.tool_runtime || {}) {
  const backend = String(runtime.backend || "");
  const mavHome = drone?.home_position || {};
  if (Number.isFinite(Number(mavHome.lat)) && Number.isFinite(Number(mavHome.lon))) {
    returnHomeGps = {
      lat: Number(mavHome.lat),
      lon: Number(mavHome.lon),
      alt_m: Number(mavHome.alt || 0),
      source: mavHome.source || "MAVLink HOME_POSITION",
      backend,
    };
    return returnHomeGps;
  }
  if (returnHomeGps?.lat != null && returnHomeGps?.lon != null && returnHomeGps.backend === backend) {
    return returnHomeGps;
  }
  const current = currentDroneGeo(drone, runtime);
  if (current?.lat != null && current?.lon != null) {
    returnHomeGps = {
      lat: Number(current.lat),
      lon: Number(current.lon),
      alt_m: Number(current.alt_m || 0),
      source: isRealVehicleRuntime(runtime) ? "first reliable GPS fix" : "simulation origin",
      backend,
    };
    return returnHomeGps;
  }
  if (isRealVehicleRuntime(runtime)) return null;
  returnHomeGps = { lat: AIRSIM_HOME_LAT, lon: AIRSIM_HOME_LON, alt_m: 0, source: "AirSim origin", backend };
  return returnHomeGps;
}

function droneHeadingDeg(drone) {
  if (Number.isFinite(Number(drone.heading_deg))) return Number(drone.heading_deg);
  if (Number.isFinite(Number(drone.heading))) return Number(drone.heading);
  const yawRad = Number(drone.attitude_rad?.yaw);
  if (Number.isFinite(yawRad)) return (yawRad * 180 / Math.PI + 360) % 360;
  const yawDeg = Number(drone.attitude_euler?.yaw);
  return Number.isFinite(yawDeg) ? yawDeg : 0;
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

function angleDeltaDeg(a, b) {
  return Math.abs((((a - b) + 540) % 360) - 180);
}

function updateActiveLeg(lngLat, target = currentTargetWaypoint(lngLat)) {
  const source = maplibreMap?.getSource("active-leg-source");
  if (!source) return;
  if (!target) {
    source.setData({ type: "FeatureCollection", features: [] });
    return;
  }
  source.setData({
    type: "FeatureCollection",
    features: [{
      type: "Feature",
      properties: {},
      geometry: { type: "LineString", coordinates: [lngLat, [target.lon, target.lat]] },
    }],
  });
}

function clearActiveLeg() {
  const source = maplibreMap?.getSource("active-leg-source");
  if (source) source.setData({ type: "FeatureCollection", features: [] });
}

function refreshActiveLegFromCurrentPosition() {
  if (!SHOW_ACTIVE_LEG) {
    clearActiveLeg();
    return;
  }
  const lngLat = droneRenderedLngLat || droneLastTelemetryLngLat;
  if (!lngLat) return;
  updateActiveLeg(lngLat);
}

function currentTargetWaypoint(lngLat) {
  if (!missionWaypoints.length || !Array.isArray(lngLat)) return null;
  const targetIndex = currentTargetWaypointIndex(lngLat);
  return targetIndex >= 0 ? missionWaypoints[targetIndex] : null;
}

function currentTargetWaypointIndex(lngLat) {
  if (!Array.isArray(lngLat)) return -1;
  if (!missionWaypoints.length) {
    activeTargetRouteKey = "";
    activeTargetIndex = 0;
    return -1;
  }
  const points = missionWaypoints
    .map((wp, index) => ({ wp, index }))
    .filter((item) => item.wp.lat != null && item.wp.lon != null);
  if (!points.length) {
    activeTargetRouteKey = "";
    activeTargetIndex = 0;
    return -1;
  }

  const routeKey = missionRouteKey(points);
  if (routeKey !== activeTargetRouteKey) {
    activeTargetRouteKey = routeKey;
    activeTargetIndex = 0;
  }

  if (!missionExecutionActive) {
    return points[0].index;
  }

  const current = { lat: Number(lngLat[1]), lon: Number(lngLat[0]) };
  while (activeTargetIndex < points.length) {
    const target = points[activeTargetIndex].wp;
    const distance = haversineMeters(current.lat, current.lon, target.lat, target.lon);
    const accept = Math.max(2.5, Number(target.acceptance_radius_m || 2) + 1.0);
    if (distance <= accept) {
      activeTargetIndex += 1;
    } else {
      break;
    }
  }

  if (activeTargetIndex >= points.length) {
    missionExecutionActive = false;
    return -1;
  }
  return points[activeTargetIndex].index;
}

function missionRouteKey(points) {
  return points
    .map(({ wp }) => `${round6(wp.lat)}:${round6(wp.lon)}`)
    .join("|");
}

function nedToGps(northM, eastM, downM) {
  const dLat = northM / EARTH_RADIUS_M * (180 / Math.PI);
  const dLon = eastM / (EARTH_RADIUS_M * Math.cos(AIRSIM_HOME_LAT * Math.PI / 180)) * (180 / Math.PI);
  return {
    lat: AIRSIM_HOME_LAT + dLat,
    lon: AIRSIM_HOME_LON + dLon,
    alt: -downM,
  };
}

// GPS → NED 反向转换
function gpsToNed(lat, lon, downM) {
  const dLat = (lat - AIRSIM_HOME_LAT) * Math.PI / 180;
  const dLon = (lon - AIRSIM_HOME_LON) * Math.PI / 180;
  const northM = dLat * EARTH_RADIUS_M;
  const eastM = dLon * EARTH_RADIUS_M * Math.cos(AIRSIM_HOME_LAT * Math.PI / 180);
  return { x: northM, y: eastM, z: downM };
}

function round6(value) {
  return Math.round(Number(value || 0) * 1e6) / 1e6;
}

function haversineMeters(lat1, lon1, lat2, lon2) {
  const toRad = (v) => (v * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function calculateBearing(lat1, lon1, lat2, lon2) {
  const toRad = (v) => (v * Math.PI) / 180;
  const dLon = toRad(lon2 - lon1);
  const y = Math.sin(dLon) * Math.cos(toRad(lat2));
  const x =
    Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
    Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(dLon);
  return (Math.atan2(y, x) * (180 / Math.PI) + 360) % 360;
}

function getDroneLatLon(drone) {
  const runtime = latestState?.tool_runtime || {};
  if (!hasReliableVehicleMapPosition(drone, runtime)) return null;
  const gps = drone?.gps;
  if (gps && gps.lat != null && gps.lon != null) {
    return { lat: Number(gps.lat), lon: Number(gps.lon) };
  }
  const pos = drone?.position_ned || { x: 0, y: 0, z: 0 };
  return nedToGps(Number(pos.x || 0), Number(pos.y || 0), Number(pos.z || 0));
}

function formatSigned(value, unit) {
  if (value == null || Number.isNaN(value)) return "--";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${fmt(Math.abs(value))}${unit ? ` ${unit}` : ""}`;
}

function computeMissionDistance() {
  if (missionWaypoints.length < 2) return 0;
  let total = 0;
  for (let i = 1; i < missionWaypoints.length; i++) {
    const a = missionWaypoints[i - 1];
    const b = missionWaypoints[i];
    if (a.lat != null && a.lon != null && b.lat != null && b.lon != null) {
      total += haversineMeters(a.lat, a.lon, b.lat, b.lon);
    }
  }
  return total;
}

function formatDistance(meters) {
  if (meters >= 1000) return `${(meters / 1000).toFixed(2)} km`;
  return `${Math.round(meters)} m`;
}

function formatDuration(seconds) {
  if (seconds >= 3600) return `${(seconds / 3600).toFixed(1)} h`;
  if (seconds >= 60) return `${Math.floor(seconds / 60)} m ${Math.round(seconds % 60)} s`;
  return `${Math.round(seconds)} s`;
}

function niceDistance(meters) {
  if (meters <= 0) return 10;
  const steps = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000];
  for (const s of steps) {
    if (s >= meters) return s;
  }
  return Math.pow(10, Math.ceil(Math.log10(meters)));
}

function drawMissionProfile() {
  const canvas = els.profileCanvas;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const width = rect.width;
  const height = rect.height;

  ctx.clearRect(0, 0, width, height);

  // 折叠或高度不足时只清空，不重绘
  if (height < 60) return;

  const padding = { top: 16, right: 16, bottom: 38, left: 50 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;

  // 动态 X 轴范围：使用当前地图可视宽度，让剖面图随卫星图缩放变化
  const visibleDist = getVisibleMapDistanceMeters();

  // 计算航点累计距离
  const points = [];
  let cumulative = 0;
  if (missionWaypoints.length >= 2) {
    points.push({ dist: 0, alt: missionWaypoints[0].alt_m || 0, seq: 1 });
    for (let i = 1; i < missionWaypoints.length; i++) {
      const a = missionWaypoints[i - 1];
      const b = missionWaypoints[i];
      const segment = haversineMeters(a.lat, a.lon, b.lat, b.lon);
      cumulative += segment;
      points.push({ dist: cumulative, alt: b.alt_m || 0, seq: i + 1 });
    }
  }

  const maxDist = Math.max(visibleDist, cumulative || 1);

  const alts = points.length ? points.map((p) => p.alt) : [0, 10];
  let minAlt = Math.min(...alts);
  let maxAlt = Math.max(...alts);
  if (maxAlt - minAlt < 5) {
    const mid = (minAlt + maxAlt) / 2;
    minAlt = mid - 2.5;
    maxAlt = mid + 2.5;
  }
  const altRange = Math.max(1, maxAlt - minAlt);

  const xFor = (d) => padding.left + (d / maxDist) * chartW;
  const yFor = (a) => padding.top + chartH - ((a - minAlt) / altRange) * chartH;

  // Y 轴水平网格线
  ctx.strokeStyle = "rgba(255,255,255,0.06)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padding.top + (chartH * i) / 4;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(padding.left + chartW, y);
    ctx.stroke();
  }

  // X 轴垂直网格线与主刻度（根据像素间距自动避免标签重叠）
  let xStep = niceDistance(maxDist / 5);
  const minPxBetweenLabels = 56;
  const ticksCount = maxDist / xStep;
  const pxPerTick = chartW / ticksCount;
  if (xStep > 0 && pxPerTick < minPxBetweenLabels) {
    xStep = xStep * Math.ceil(minPxBetweenLabels / pxPerTick);
  }
  ctx.strokeStyle = "rgba(255,255,255,0.05)";
  for (let d = 0; d <= maxDist + 0.001; d += xStep) {
    const x = xFor(d);
    ctx.beginPath();
    ctx.moveTo(x, padding.top);
    ctx.lineTo(x, padding.top + chartH);
    ctx.stroke();
  }

  if (points.length >= 2) {
    // 高度填充区域
    ctx.beginPath();
    ctx.moveTo(xFor(points[0].dist), yFor(points[0].alt));
    for (let i = 1; i < points.length; i++) ctx.lineTo(xFor(points[i].dist), yFor(points[i].alt));
    ctx.lineTo(xFor(points[points.length - 1].dist), padding.top + chartH);
    ctx.lineTo(xFor(points[0].dist), padding.top + chartH);
    ctx.closePath();
    const gradient = ctx.createLinearGradient(0, padding.top, 0, padding.top + chartH);
    gradient.addColorStop(0, "rgba(85, 223, 244, 0.35)");
    gradient.addColorStop(1, "rgba(85, 223, 244, 0.04)");
    ctx.fillStyle = gradient;
    ctx.fill();

    // 高度线
    ctx.beginPath();
    ctx.moveTo(xFor(points[0].dist), yFor(points[0].alt));
    for (let i = 1; i < points.length; i++) ctx.lineTo(xFor(points[i].dist), yFor(points[i].alt));
    ctx.strokeStyle = "#55dff4";
    ctx.lineWidth = 2;
    ctx.stroke();

    // 航点标记
    points.forEach((p) => {
      const x = xFor(p.dist);
      const y = yFor(p.alt);
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fillStyle = selectedWaypointIndex + 1 === p.seq ? "#f0b84a" : "#55dff4";
      ctx.fill();
      ctx.strokeStyle = "#06121a";
      ctx.lineWidth = 1.5;
      ctx.stroke();

      ctx.fillStyle = "rgba(237, 244, 255, 0.8)";
      ctx.font = "10px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(String(p.seq), x, y - 8);
    });
  } else {
    // 空状态：轻量网格背景 + 提示
    ctx.strokeStyle = "rgba(255,255,255,0.05)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const x = padding.left + (chartW * i) / 4;
      ctx.beginPath();
      ctx.moveTo(x, padding.top);
      ctx.lineTo(x, padding.top + chartH);
      ctx.stroke();
    }

    // 中央提示文字
    ctx.fillStyle = "rgba(237, 244, 255, 0.72)";
    ctx.font = "12px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("点击地图添加航点", width / 2, height / 2 - 8);
    ctx.fillStyle = "rgba(141, 152, 173, 0.6)";
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillText("至少两个航点后可显示高度/距离剖面", width / 2, height / 2 + 10);
  }

  // 比例尺：根据地图当前缩放级别计算固定像素长度对应的地面距离
  function getMapScale() {
    if (!maplibreMap) return null;
    try {
      const metersPerPx = 1 / (maplibreMap.transform._pixelPerMeter || 1);
      const targetPx = 70;
      const rawDist = metersPerPx * targetPx;
      const dist = niceDistance(rawDist);
      const px = dist / metersPerPx;
      return { dist, px };
    } catch (err) {
      return null;
    }
  }
  const scale = getMapScale();
  if (scale) {
    const label = formatDistance(scale.dist);
    const labelWidth = ctx.measureText(label).width;
    const sx = width - padding.right - Math.max(scale.px, labelWidth) - 10;
    const sy = padding.top + 14;
    // 小背景提升可读性
    ctx.fillStyle = "rgba(7, 9, 15, 0.65)";
    ctx.fillRect(sx - 4, sy - 16, Math.max(scale.px, labelWidth) + 10, 22);
    ctx.strokeStyle = "rgba(237, 244, 255, 0.9)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(sx, sy);
    ctx.lineTo(sx + scale.px, sy);
    ctx.stroke();
    ctx.fillStyle = "rgba(237, 244, 255, 0.95)";
    ctx.font = "bold 10px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    ctx.fillText(label, sx, sy - 4);
  }

  // 坐标轴文字
  ctx.fillStyle = "rgba(141, 152, 173, 0.8)";
  ctx.font = "10px system-ui, sans-serif";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let i = 0; i <= 4; i++) {
    const alt = minAlt + (altRange * i) / 4;
    const y = padding.top + chartH - (chartH * i) / 4;
    ctx.fillText(`${Math.round(alt)}m`, padding.left - 6, y);
  }
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (let d = 0; d <= maxDist + 0.001; d += xStep) {
    const x = xFor(d);
    ctx.fillText(formatDistance(d), x, padding.top + chartH + 5);
  }

  // 轴标签
  ctx.save();
  ctx.translate(10, padding.top + chartH / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = "center";
  ctx.fillStyle = "rgba(141, 152, 173, 0.7)";
  ctx.fillText("高度", 0, 0);
  ctx.restore();

  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";
  ctx.fillStyle = "rgba(141, 152, 173, 0.7)";
  ctx.fillText("距离", padding.left + chartW / 2, height - 6);
}

function getVisibleMapDistanceMeters() {
  if (!maplibreMap) return 1000;
  try {
    const bounds = maplibreMap.getBounds();
    const centerLat = bounds.getCenter().lat;
    const dLon = (bounds.getEast() - bounds.getWest()) * Math.PI / 180;
    return Math.max(10, Math.abs(dLon) * EARTH_RADIUS_M * Math.cos(centerLat * Math.PI / 180));
  } catch (err) {
    return 1000;
  }
}

function drawMapTexture(ctx, width, height) {
  const gradient = ctx.createLinearGradient(0, 0, width, height);
  gradient.addColorStop(0, "#05070b");
  gradient.addColorStop(1, "#10131b");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, width, height);

  ctx.save();
  ctx.translate(width * 0.5, height * 0.5);
  ctx.rotate(-0.42);
  ctx.translate(-width * 0.5, -height * 0.5);

  ctx.strokeStyle = "rgba(255,255,255,0.055)";
  ctx.lineWidth = 2;
  for (let x = -width; x < width * 2; x += 54) {
    line(ctx, x, -height, x + height * 0.35, height * 2);
  }
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  for (let y = -height; y < height * 2; y += 72) {
    line(ctx, -width, y, width * 2, y + width * 0.1);
  }

  ctx.strokeStyle = "rgba(85, 223, 244, 0.10)";
  ctx.lineWidth = 1;
  for (let x = -width; x < width * 2; x += 135) {
    line(ctx, x, -height, x + height * 0.28, height * 2);
  }
  ctx.restore();
}

function drawGeofence(ctx, cx, cy, scale) {
  ctx.strokeStyle = "rgba(237,244,255,0.16)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(cx, cy, 100 * scale, 0, Math.PI * 2);
  ctx.stroke();

  ctx.strokeStyle = "rgba(85,223,244,0.55)";
  ctx.lineWidth = 1;
  line(ctx, cx - 16, cy, cx + 16, cy);
  line(ctx, cx, cy - 16, cx, cy + 16);

  ctx.fillStyle = "rgba(85,223,244,0.75)";
  ctx.font = "12px Segoe UI, sans-serif";
  ctx.fillText("HOME", cx + 10, cy - 10);
}

function drawPath(ctx, points, cx, cy, scale, color, numbered) {
  if (!points.length) return;

  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 2;
  ctx.setLineDash([7, 8]);
  ctx.beginPath();
  points.forEach((pt, index) => {
    const px = cx + Number(pt.y || 0) * scale;
    const py = cy - Number(pt.x || 0) * scale;
    if (index === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  if (points.length > 2) {
    const first = points[0];
    ctx.lineTo(cx + Number(first.y || 0) * scale, cy - Number(first.x || 0) * scale);
  }
  ctx.stroke();
  ctx.setLineDash([]);

  points.forEach((pt, index) => {
    const px = cx + Number(pt.y || 0) * scale;
    const py = cy - Number(pt.x || 0) * scale;
    ctx.beginPath();
    ctx.arc(px, py, 14, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(6, 18, 26, 0.92)";
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = color;
    ctx.stroke();
    if (numbered) {
      ctx.fillStyle = "#eaffff";
      ctx.font = "bold 12px Segoe UI, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(index + 1), px, py);
      ctx.textAlign = "left";
      ctx.textBaseline = "alphabetic";
    }
  });
}

function drawDrone(ctx, px, py, heading) {
  ctx.save();
  ctx.translate(px, py);
  ctx.rotate(heading);
  ctx.shadowColor = "rgba(85, 223, 244, 0.72)";
  ctx.shadowBlur = 18;
  ctx.fillStyle = "#a78bfa";
  ctx.strokeStyle = "#55dff4";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(0, -20);
  ctx.lineTo(14, 16);
  ctx.lineTo(0, 8);
  ctx.lineTo(-14, 16);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#4ee6a4";
  ctx.fillRect(-4, -14, 8, 24);
  ctx.restore();
}

function extractPlanWaypoints(run) {
  if (!run || !run.plan) return [];
  const points = [];
  for (const step of run.plan.steps || []) {
    if (step.tool === "drone_fly_to") {
      points.push({ x: Number(step.params.x || 0), y: Number(step.params.y || 0), z: Number(step.params.z || -3) });
    }
    if (step.tool === "drone_fly_path" && step.params.waypoints_json) {
      try {
        JSON.parse(step.params.waypoints_json).forEach((wp) => {
          points.push({ x: Number(wp.x || 0), y: Number(wp.y || 0), z: Number(wp.z || -3) });
        });
      } catch (_) {
        return points;
      }
    }
  }
  return points;
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

function initSplitters() {
  document.querySelectorAll("[data-splitter]").forEach((splitter) => {
    splitter.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      const kind = splitter.dataset.splitter;
      const shellRect = els.appShell.getBoundingClientRect();
      const mapColumn = document.querySelector(".map-column");
      const mapRect = mapColumn.getBoundingClientRect();
      document.body.classList.add("resizing");
      splitter.setPointerCapture(event.pointerId);

      const move = (moveEvent) => {
        if (kind === "left") {
          const next = clamp(moveEvent.clientX - shellRect.left - 12, 250, 560);
          document.documentElement.style.setProperty("--left-pane", `${next}px`);
          saveLayoutPref("left", Math.round(next));
        } else if (kind === "right") {
          const next = clamp(shellRect.right - moveEvent.clientX - 12, 310, 660);
          document.documentElement.style.setProperty("--right-pane", `${next}px`);
          saveLayoutPref("right", Math.round(next));
        } else if (kind === "timeline") {
          const next = clamp(mapRect.bottom - moveEvent.clientY, 130, 380);
          document.documentElement.style.setProperty("--timeline-height", `${next}px`);
          saveLayoutPref("timeline", Math.round(next));
        }
        if (maplibreMap) maplibreMap.resize();
      };

      const up = () => {
        document.body.classList.remove("resizing");
        splitter.releasePointerCapture(event.pointerId);
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };

      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  });
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
  if (vehicleSettingsAvailable()) await loadVehicleSetup(false);
  selectedConnectionId = activeConnectionId || connectionsCache[0]?.id || "";
  renderConnectionsList();
  renderConnectionDetail(selectedConnectionId);
  renderVehicleSettingsPanel();
  setSystemSettingsSection(activeSystemSettingsSection || "links");
  renderSystemConnection();
  loadAirSimSettingsTemplates();
}

// ── AirSim settings.json（按连接类型联动，详情内嵌 + 共享 detail-footer 应用按钮） ──
let airsimTemplatesLoaded = false;
let airsimTemplatesCache = [];
let airsimTemplateSelected = "";

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

// 经典 JSON 语法高亮 (highlight.js 同款正则), 改为每行调用一次以保留行号结构.
const JSON_TOKEN_RE = /("(?:\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(?:\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?|[{}\[\],])/g;

function highlightJsonLine(line) {
  if (!line) return "";
  // 先 HTML 转义, 再用正则匹配插入 span.
  const escaped = line
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return escaped.replace(JSON_TOKEN_RE, (match) => {
    let cls = "";
    if (match.startsWith('"')) {
      cls = /:\s*$/.test(match) ? "json-key" : "json-string";
    } else if (/^(?:true|false)$/.test(match)) {
      cls = "json-boolean";
    } else if (match === "null") {
      cls = "json-null";
    } else if (/^-?\d/.test(match)) {
      cls = "json-number";
    } else {
      cls = "json-punct";
    }
    return `<span class="${cls}">${match}</span>`;
  });
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

function initAirSimTemplatesEvents() {
  document.getElementById("airsimTemplateApply")?.addEventListener("click", applyAirSimSettingsTemplate);
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
  if (isVehicleSetupSection(section) && !vehicleSettingsAvailable()) {
    showNotice("Vehicle Settings 需要已连接的 PX4 数据源", "error");
    section = "links";
  }
  activeSystemSettingsSection = section || "links";
  const modal = els.systemSettingsModal || document;
  modal.querySelectorAll("[data-system-section]").forEach((button) => {
    button.classList.toggle("active", button.dataset.systemSection === activeSystemSettingsSection);
  });
  modal.querySelectorAll("[data-system-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.systemPanel === activeSystemSettingsSection);
  });
  if (isVehicleSetupSection(activeSystemSettingsSection)) {
    renderActiveVehicleSetupPanel("section");
    startVehicleSetupPolling();
  } else {
    stopVehicleSetupPolling();
  }
  if (activeSystemSettingsSection === "vehicle") {
    renderVehicleSettingsPanel();
    loadVehicleInfo(false).catch(() => {});
  }
  if (activeSystemSettingsSection === "parameters") {
    renderVehicleParametersPanel();
    loadVehicleParameters(false).catch(() => {});
  }
  if (activeSystemSettingsSection === "links") {
    renderConnectionsList();
    renderConnectionDetail(selectedConnectionId);
  }
}

function screenToNed(px, py) {
  return {
    x: (mapTransform.cy - py) / mapTransform.scale,
    y: (px - mapTransform.cx) / mapTransform.scale,
  };
}

function currentDronePosition() {
  const drone = latestState?.tool_runtime?.drone || {};
  const pos = drone.position_ned || {};
  return {
    x: Number(pos.x || 0),
    y: Number(pos.y || 0),
    z: Number(pos.z || 0),
  };
}

function findConstraints() {
  const run = latestState && latestState.current_run;
  const steps = run && run.plan ? run.plan.steps : [];
  for (const step of steps || []) {
    if (step.safety && step.safety.constraints) return step.safety.constraints;
  }
  return { max_altitude: 50, max_velocity: 8, geofence_radius: 100 };
}

function parseParams(raw) {
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch (_) {
    return {};
  }
}

function statusClass(status) {
  if (["failed", "blocked"].includes(status)) return "danger";
  if (["paused", "planned", "queued"].includes(status)) return "warn";
  if (["running", "responding", "completed"].includes(status)) return "strong";
  return "";
}

function fmt(value) {
  const n = Number(value || 0);
  return n.toFixed(Math.abs(n) >= 10 ? 0 : 1);
}

function round1(value) {
  return Math.round(Number(value || 0) * 10) / 10;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function line(ctx, x1, y1, x2, y2) {
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderMarkdown(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const blocks = text.split(/\n{2,}/).map((block) => block.trim()).filter(Boolean);
  return blocks.map((block) => {
    const lines = block.split(/\n/).map((line) => line.trim()).filter(Boolean);
    if (lines.length && lines.every((line) => /^[-*]\s+/.test(line))) {
      return `<ul>${lines.map((line) => `<li>${renderInlineMarkdown(line.replace(/^[-*]\s+/, ""))}</li>`).join("")}</ul>`;
    }
    if (lines.length && lines.every((line) => /^\d+[.)]\s+/.test(line))) {
      return `<ol>${lines.map((line) => `<li>${renderInlineMarkdown(line.replace(/^\d+[.)]\s+/, ""))}</li>`).join("")}</ol>`;
    }
    // markdown 表格：首行表头，第二行 |---|---| 分隔，其余为数据行
    if (lines.length >= 2 && lines.every((line) => /^\|.*\|$/.test(line)) && /^\|[\s:|-]+\|$/.test(lines[1])) {
      const parseRow = (row) => row.replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
      const headers = parseRow(lines[0]);
      const rows = lines.slice(2).map(parseRow);
      const cell = (value, tag) => `<${tag}>${renderInlineMarkdown(value)}</${tag}>`;
      return `<table class="md-table"><thead><tr>${headers.map((h) => cell(h, "th")).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${headers.map((_, i) => cell(r[i] ?? "", "td")).join("")}</tr>`).join("")}</tbody></table>`;
    }
    const normalized = block.replace(/^#{1,6}\s+/gm, "");
    return `<p>${normalized.split(/\n/).map((line) => renderInlineMarkdown(line)).join("<br>")}</p>`;
  }).join("");
}

function renderInlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function showNotice(message, level = "info") {
  if (!els.notice) return;
  window.clearTimeout(noticeTimer);
  els.notice.hidden = false;
  els.notice.textContent = message;
  els.notice.className = `notice ${level}`;
  noticeTimer = window.setTimeout(() => {
    els.notice.hidden = true;
  }, level === "error" ? 6000 : 3200);
}

refresh().catch((error) => showNotice(error.message || "状态加载失败", "error"));

// ---------------------------------------------------------------------------
// 会话流渲染（增量持久节点版）
//
// 此前的全量 innerHTML 重建有两个致命问题：思考块的展开状态随重建丢失
// （"过一会自动折叠"）、整个对话闪烁。现在每条消息一个持久 DOM 节点
// （turnNodes 按 message.id 索引），增量更新内部区块：
//   ┌ turn ─────────────────────────────┐
//   │ ▸ 思考块（默认折叠，标题滚动最新一句，展开看全文）│
//   │ ✓ 工具/校验步骤（单行，追加式）              │
//   │ [最终回答 markdown（平滑分批释放）]           │
//   └───────────────────────────────────┘
// ---------------------------------------------------------------------------

