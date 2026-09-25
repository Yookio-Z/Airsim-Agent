// ui-chat.js —— 会话列表、对话气泡、思考/执行过程与输入记录导航条
// 由 app.js 拆分而来；各文件共享同一份脚本作用域，按顺序加载。

function pinChatToBottom() {
  if (!els.chatThread) return;
  chatProgrammaticScrollAt = Date.now();
  els.chatThread.scrollTop = els.chatThread.scrollHeight;
  chatAutoFollow = true;
}
// ── 从实际链路反推"当前连的是用户加的那条连接" ─────────────────────────
// 监听模式（udpin:0.0.0.0:PORT）会把配置里的 host 丢掉：JETSON(192.168.137.217)
// 和 WSL(127.0.0.1) 都用 14550 时，展开出来是同一个监听口，光看 URL 分不出是
// 哪条。所以要用真实心跳来源（actual_peer_endpoint）配合本地端口来认领，
// 而不是按"列表里第一条同后端的预设"硬指一条。
function normalizeHost(value) {
  return String(value || "").trim().toLowerCase().replace(/^\[|\]$/g, "");
}

function isLoopbackHost(host) {
  return ["localhost", "127.0.0.1", "::1", "0.0.0.0"].includes(normalizeHost(host));
}

function isPrivateHost(host) {
  const value = normalizeHost(host);
  if (isLoopbackHost(value)) return true;
  if (/^10\./.test(value) || /^192\.168\./.test(value) || /^169\.254\./.test(value)) return true;
  return /^172\.(1[6-9]|2\d|3[01])\./.test(value);
}

function hostMatches(left, right) {
  const a = normalizeHost(left);
  const b = normalizeHost(right);
  if (!a || !b) return false;
  if (a === b) return true;
  const loopbacks = new Set(["localhost", "127.0.0.1", "::1"]);
  return loopbacks.has(a) && loopbacks.has(b);
}

function peerEndpointParts(link = currentActualLink()) {
  const endpoint = String(link.actual_peer_endpoint || "");
  const index = endpoint.lastIndexOf(":");
  if (index <= 0) return { host: "", port: 0 };
  const port = Number(endpoint.slice(index + 1));
  return { host: normalizeHost(endpoint.slice(0, index)), port: Number.isFinite(port) ? port : 0 };
}

function syncCameraToolbarState() {
  const visible = visibleCameraWindows().length > 0;
  if (!els.cameraViewBtn) return;
  els.cameraViewBtn.setAttribute("aria-pressed", String(visible));
  els.cameraViewBtn.classList.toggle("active", visible);
  els.cameraViewBtn.title = visible ? "隐藏摄像头窗口" : "显示摄像头窗口";
  els.cameraViewBtn.setAttribute("aria-label", visible ? "隐藏摄像头窗口" : "显示摄像头窗口");
}

function setupCameraWindowEvents(win) {
  if (!win || win.eventsBound) return;
  win.eventsBound = true;
  win.el.addEventListener("pointerdown", () => focusCameraWindow(win));
  win.newBtn?.addEventListener("click", () => createAdditionalCameraWindow(win));
  // Click the image to cycle zoom (1x -> 1.6x -> 2.4x -> reset); 以鼠标位置为锚点放大，
  // 容器滚动以便查看细节，且光标下的画面内容在缩放前后保持不动。
  win.imageEl?.addEventListener("click", (event) => {
    const container = win.imageEl.parentElement;
    if (!container) return;
    const cur = win.imgScale || 1;
    const next = cur >= 2.4 ? 1 : cur >= 1.6 ? 2.4 : 1.6;
    win.imgScale = next;
    if (next <= 1) {
      // 复位：清空缩放，恢复裁剪（面板缩放时图片始终自适应铺满）
      win.imageEl.style.transform = "";
      win.imageEl.style.transformOrigin = "";
      win.imageEl.style.cursor = "zoom-in";
      container.style.overflow = "hidden";
      container.scrollTop = 0;
      container.scrollLeft = 0;
      return;
    }
    const rect = container.getBoundingClientRect();
    const cx = event.clientX - rect.left;
    const cy = event.clientY - rect.top;
    // 光标当前对应的图像内容坐标（未缩放布局像素）
    const contentX = (container.scrollLeft + cx) / cur;
    const contentY = (container.scrollTop + cy) / cur;
    win.imageEl.style.transformOrigin = "0 0";
    win.imageEl.style.transform = `scale(${next})`;
    win.imageEl.style.cursor = "zoom-out";
    container.style.overflow = "auto";
    // 触发一次布局让可滚动范围更新，再把同一内容点滚回光标下
    void container.scrollWidth;
    container.scrollLeft = contentX * next - cx;
    container.scrollTop = contentY * next - cy;
  });
  win.closeBtn?.addEventListener("click", () => stopCameraStream({ hide: true, windowId: win.id }));
  win.sourceSelect?.addEventListener("change", (event) => {
    toggleCameraSourceSpecificFields(win);
    updateCameraViewerSelection(event);
  });
  [win.cameraSelect, win.vehicleSelect, win.imageTypeSelect]
    .filter(Boolean)
    .forEach((control) => control.addEventListener("change", updateCameraViewerSelection));
  setupCameraWindowDrag(win);
}

function setupCameraEventListeners() {
  bindCameraViewerResize();
  const primary = ensurePrimaryCameraWindow();
  if (els.cameraViewBtn) {
    els.cameraViewBtn.addEventListener("click", () => {
      if (visibleCameraWindows().length) stopAllCameraStreams({ hide: true });
      else startCameraStream({ windowId: primary?.id || "camera_1" });
    });
  }
  if (els.cameraSaveSettingsBtn) {
    els.cameraSaveSettingsBtn.addEventListener("click", () => saveCameraSettings());
  }
  if (els.cameraCaptureFromSettingsBtn) {
    els.cameraCaptureFromSettingsBtn.addEventListener("click", () => {
      const win = primaryCameraWindow();
      if (win) win.settings = readCameraSettingsForm();
      startCameraStream({ windowId: win?.id || "camera_1", settings: win?.settings || cameraSettings });
    });
  }
  window.addEventListener("resize", () => clampCameraViewerPosition());
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      cameraWindows.forEach((win) => {
        if (win.timer) clearTimeout(win.timer);
        win.timer = null;
      });
      return;
    }
    cameraWindows.forEach((win) => {
      if (win.streamActive && cameraViewerIsVisible(win)) scheduleCameraFrame(win, 0);
    });
  });
  [els.cameraSource, els.cameraRtspUrl, els.cameraRtspTransport, els.cameraName, els.cameraVehicle, els.cameraImageType, els.cameraTimeout, els.cameraAutoSave]
    .filter(Boolean)
    .forEach((control) => {
      control.addEventListener("change", () => {
        cameraSettings = readCameraSettingsForm();
        const win = primaryCameraWindow();
        if (win) {
          win.settings = { ...cameraSettings };
          syncCameraWindowControls(win);
          renderCameraMeta(null, win);
        }
        renderCameraSettings();
      });
    });
}

function loadAutoConnectEnabled() {
  return autoConnectEnabled;
}

function setupConnectionEventListeners() {
  if (els.addConnectionBtn) {
    els.addConnectionBtn.addEventListener("click", () => {
      renderConnectionDetail("");
      if (els.connectionDetailName) els.connectionDetailName.focus();
    });
  }
  if (els.connectionDetailType) {
    els.connectionDetailType.addEventListener("change", updateConnectionTypeFields);
  }
  // 勾选真实飞控后要立刻收起 SITL 专用的远端端口项，不能等切类型才刷新。
  if (els.connectionDetailRealVehicle) {
    els.connectionDetailRealVehicle.addEventListener("change", updateConnectionTypeFields);
  }
  if (els.connectionDetailForm) {
    els.connectionDetailForm.addEventListener("submit", submitConnectionDetail);
  }
  if (els.connectionDetailConnect) {
    els.connectionDetailConnect.addEventListener("click", activateSelectedConnection);
  }
  if (els.connectionDetailDelete) {
    els.connectionDetailDelete.addEventListener("click", deleteSelectedConnection);
  }
  if (els.connectionDetailCancel) {
    els.connectionDetailCancel.addEventListener("click", closeSystemSettings);
  }
  if (els.connectionsList) {
    els.connectionsList.addEventListener("click", (event) => {
      const item = event.target.closest(".connection-item");
      if (!item) return;
      const connId = item.dataset.connectionId;
      renderConnectionDetail(connId);
    });
    els.connectionsList.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const item = event.target.closest(".connection-item");
      if (!item) return;
      event.preventDefault();
      const connId = item.dataset.connectionId;
      renderConnectionDetail(connId);
    });
  }
}

function closeAllDropdowns() {
  closeComposerMenus();
  if (els.sessionMenu) {
    els.sessionMenu.hidden = true;
    els.sessionSwitcher?.classList.remove("open");
    els.sessionSwitcherBtn?.setAttribute("aria-expanded", "false");
  }
}

function toggleSessionMenu() {
  if (!els.sessionMenu) return;
  const willOpen = els.sessionMenu.hidden;
  closeSessionsPanel();
  closeComposerMenus();
  if (willOpen) renderSessionMenu();
  els.sessionMenu.hidden = !willOpen;
  els.sessionSwitcher?.classList.toggle("open", willOpen);
  els.sessionSwitcherBtn?.setAttribute("aria-expanded", String(willOpen));
}

function extractApiError(data, fallback = "request failed") {
  const candidates = [
    data?.error,
    data?.message,
    data?.result?.message,
    data?.result?.data?.message,
    data?.result?.data?.data?.message,
  ];
  for (const item of candidates) {
    if (typeof item === "string" && item.trim()) return item;
  }
  const violations =
    data?.violations ||
    data?.result?.violations ||
    data?.result?.data?.violations ||
    data?.result?.data?.safety?.violations ||
    data?.result?.data?.data?.violations;
  if (Array.isArray(violations) && violations.length) {
    return violations.filter(Boolean).join("; ");
  }
  return fallback;
}

function isLiveRunStatus(status) {
  return ["queued", "running", "paused", "responding", "awaiting_approval"].includes(String(status || ""));
}

function isAgentWorkActive() {
  const run = latestState?.current_run;
  if (run && isLiveRunStatus(run.status)) return true;
  // 乐观 pending 气泡也代表"已有工作在途"：提交后不再同步 refresh 整页状态，
  // SSE 消息到达前的短暂窗口里，发送键/中断语义靠这里保持一致。
  if (localPendingMessages.some((message) => message?.role === "assistant" && message.status === "running")) return true;
  const messages = Array.isArray(latestState?.messages) ? latestState.messages : [];
  return messages.some((message) => (
    message?.role === "assistant"
    && message?.status === "running"
    && ["chat", "execute", "plan"].includes(String(message?.details?.mode || ""))
  ));
}

// 是否多机规划模式（后端报了多架车）
function isMultiVehiclePlanning() {
  const vehicles = Array.isArray(latestState?.tool_runtime?.vehicles) ? latestState.tool_runtime.vehicles : [];
  return vehicles.length > 1;
}

function controlTargetLabel() {
  return controlSelectionVehicle || "全部无人机";
}

function interpolateLngLat(from, to, t) {
  return [
    Number(from[0]) + (Number(to[0]) - Number(from[0])) * t,
    Number(from[1]) + (Number(to[1]) - Number(from[1])) * t,
  ];
}

function normalizeHeadingDeg(value) {
  const n = Number(value);
  return Number.isFinite(n) ? ((n % 360) + 360) % 360 : 0;
}

function interpolateHeadingDeg(from, to, t) {
  const start = normalizeHeadingDeg(from);
  const delta = (((normalizeHeadingDeg(to) - start) + 540) % 360) - 180;
  return normalizeHeadingDeg(start + delta * t);
}

function activeFlightRuntime() {
  return latestState?.tool_runtime || {};
}

function approvalCommandForTool(tool, params = {}) {
  if (tool === "drone_arm") return "解锁无人机";
  if (tool === "drone_disarm") return "上锁（锁定电机）";
  if (tool === "drone_takeoff") return `起飞到 ${Number(params.altitude || 3)} 米并悬停`;
  if (tool === "drone_land") return "降落无人机";
  return "";
}

async function invokeFlightTool(tool, params = {}) {
  const runtime = requireLiveFlightLink();
  const capabilities = runtime.backend_profile?.capabilities || {};
  const approvalCommand = approvalCommandForTool(tool, params);
  if (approvalCommand && capabilities.real_vehicle && capabilities.requires_operator_approval) {
    const approved = await confirmDialog({
      title: "确认真实飞控操作",
      message: `${approvalCommand}。当前通道：${runtime.operation_contract?.command_channel || runtime.backend}。`,
      confirmLabel: "确认执行",
      danger: true,
    });
    if (!approved) throw new Error("操作已取消");
  }
  // 空中上锁会切断动力：AirSim 的 armDisarm(False) 会让飞机失去升力，PX4 会拒绝
  // 非强制的空中上锁，但不能指望后端一定拒绝。工具栏上"解锁/上锁"是同一个按钮，
  // 误点一下就是掉机，所以只要飞机在空中就再确认一次。
  if (tool === "drone_disarm" && runtime.drone?.flying) {
    const altitude = Math.abs(Number(runtime.drone?.position_ned?.z || 0));
    const confirmed = await confirmDialog({
      title: "飞机正在空中",
      message: `当前高度约 ${altitude.toFixed(1)} 米。空中上锁会切断动力输出，飞机将失去升力。确认上锁？`,
      confirmLabel: "确认上锁",
      danger: true,
    });
    if (!confirmed) throw new Error("操作已取消");
  }
  return post("/api/tool", {
    tool,
    params,
    dry_run: false,
    expected_backend: runtime.backend,
  });
}

async function runButton(button, fn, successMessage) {
  button.disabled = true;
  try {
    const result = await fn();
    await refresh();
    showNotice(extractApiSuccess(result, successMessage), "success");
  } catch (error) {
    showNotice(error.message || "指令执行失败", "error");
  } finally {
    button.disabled = false;
    updateFlightControlButtons(activeFlightRuntime());
  }
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

function formatTokens(value) {
  if (!Number.isFinite(value)) return "--";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 10_000) return `${(value / 1000).toFixed(1)}k`;
  return value.toLocaleString();
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

function filteredSessions(sessions) {
  const term = sessionFilter.trim().toLowerCase();
  if (!term) return sessions;
  return sessions.filter((s) => String(s.name || "").toLowerCase().includes(term));
}

function setSessionFilter(value) {
  sessionFilter = String(value ?? "");
  renderSessions(latestState?.sessions || [], latestState?.current_session);
}

function sessionRowHtml(session, currentId) {
  const isActive = session.id === currentId;
  const timeText = formatSessionTime(session.updated_at || session.created_at);
  const count = Number(session.message_count || 0);
  const inputs = Number(session.input_count ?? 0);
  const counts = inputs
    ? `${inputs} 条输入 · ${count} 条消息`
    : `${count} 条消息`;
  return `
    <div class="session-item ${isActive ? "active" : ""}" data-session-id="${escapeHtml(session.id)}" data-session-action="load" role="button" tabindex="0" title="${escapeHtml(session.name || "未命名对话")}（双击改名）">
      <div class="session-name">${escapeHtml(session.name || "未命名对话")}</div>
      <div class="session-meta">
        <span>${timeText}</span>
        <span>${counts}</span>
      </div>
      <div class="session-actions" data-stop-propagation>
        <button data-session-id="${escapeHtml(session.id)}" data-session-action="rename" title="重命名" aria-label="重命名会话">✎</button>
        <button data-session-id="${escapeHtml(session.id)}" data-session-action="export" data-session-format="markdown" title="导出完整会话" aria-label="导出会话">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 21h16"/></svg>
        </button>
        <button class="delete-session" data-session-id="${escapeHtml(session.id)}" data-session-action="delete" title="删除会话" aria-label="删除会话">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
        </button>
      </div>
    </div>
  `;
}

function renderSessions(sessions, currentSession) {
  const currentId = currentSession?.id || "";
  const list = Array.isArray(sessions) ? sessions : [];
  if (els.sessionsList) {
    const visible = filteredSessions(list);
    els.sessionsList.innerHTML = visible.length
      ? visible.map((s) => sessionRowHtml(s, currentId)).join("")
      : `<div class="empty">${list.length ? "没有匹配的会话" : "暂无会话"}</div>`;
  }
  renderSessionMenu(list, currentId);
}

// Agent 面板顶部的会话下拉：直接切换 / 删除 / 新建，不必先返回会话列表
function renderSessionMenu(sessions, currentId) {
  if (!els.sessionMenu) return;
  const list = Array.isArray(sessions) ? sessions : (latestState?.sessions || []);
  const activeId = currentId ?? (latestState?.current_session?.id || "");
  const visible = filteredSessions(list);
  const rows = visible.length
    ? visible.map((s) => sessionRowHtml(s, activeId)).join("")
    : `<div class="composer-menu-empty">${list.length ? "没有匹配的会话" : "还没有会话"}</div>`;
  els.sessionMenu.innerHTML = `
    <label class="session-menu-search">
      <input id="sessionMenuSearch" type="search" placeholder="搜索会话" autocomplete="off" value="${escapeHtml(sessionFilter)}">
    </label>
    <div class="session-menu-list">${rows}</div>
    <div class="composer-menu-sep"></div>
    <button class="composer-menu-item" data-session-menu="new" type="button">
      <span class="composer-menu-main">＋ 新建会话</span>
    </button>
    <button class="composer-menu-item" data-session-menu="all" type="button">
      <span class="composer-menu-main">全部会话</span>
    </button>
  `;
}

function renderCurrentSessionLabel(currentSession) {
  if (!els.currentSessionLabel) return;
  els.currentSessionLabel.textContent = currentSession?.name || "未命名对话";
  els.currentSessionLabel.title = `${currentSession?.name || "未命名对话"}（双击重命名，点击切换会话）`;
}

function startHeaderSessionRename() {
  const currentSession = latestState?.current_session;
  if (!currentSession?.id || !els.currentSessionLabel) return;
  const sessionId = currentSession.id;
  const span = els.currentSessionLabel;
  const currentName = span.textContent;
  closeSessionsOverlays();
  renamingSession = true;
  span.hidden = true;

  const input = document.createElement("input");
  input.type = "text";
  input.className = "current-session-input";
  input.value = currentName;
  // 放进 header-left 而不是按钮内部：输入框嵌在 button 里会把点击都变成"切换会话"
  (els.sessionSwitcher?.parentNode || span.parentNode).insertBefore(input, els.sessionSwitcher || span);
  input.focus();
  input.select();

  const cleanup = () => {
    renamingSession = false;
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
      await refresh().catch(() => {});
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
  if (els.sessionSwitcher) els.sessionSwitcher.hidden = listOpen;
  if (els.chatRail) {
    els.chatRail.hidden = listOpen || Number(els.chatRail.dataset?.count || 0) < 2;
  }
}

function openSessionsPanel() {
  if (!els.sessionsPanel) return;
  closeSessionsOverlays();
  els.sessionsPanel.classList.add("is-open");
  els.sessionsPanel.hidden = false;
  els.agentColumn?.classList.add("sessions-open");
  if (els.sessionsSearch) els.sessionsSearch.value = sessionFilter;
  syncHeader();
}

function closeSessionsPanel() {
  if (!els.sessionsPanel) return;
  els.sessionsPanel.classList.remove("is-open");
  els.sessionsPanel.hidden = true;
  els.agentColumn?.classList.remove("sessions-open");
  syncHeader();
}

// 收起会话面板、会话下拉与各类浮层：切换/新建/删除会话后统一走这里
function closeSessionsOverlays() {
  closeSessionsPanel();
  closeAllDropdowns();
  hideRailTip();
}

// ── 输入记录导航条：贴在对话区左侧，一横杆 = 一条用户输入，点击跳转 ──
function railEntries(messages) {
  return (messages || [])
    .filter((message) => message?.role === "user" && message.id)
    .map((message, index) => ({
      id: message.id,
      index: index + 1,
      text: String(message.content || ""),
      time: formatSessionTime(message.created_at),
    }));
}

// 导航条内容签名（用户消息 id 序列）。内容不变时不重建 innerHTML——
// 这条栏在每次 renderChat 都会走一遍，重建属于"只加一条消息却整栏重绘"。
let chatRailSig = "";

function renderChatRail(messages) {
  const rail = els.chatRail;
  if (!rail) return;
  const entries = railEntries(messages);
  // 可见性判据只在这里维护，syncHeader 读 dataset.count，避免两处规则不一致
  rail.dataset.count = String(entries.length);
  const sig = entries.length < 2 ? "off" : entries.map((entry) => entry.id).join("\u0001");
  if (sig === chatRailSig) return;
  chatRailSig = sig;
  hideRailTip();
  if (entries.length < 2) {
    rail.hidden = true;
    rail.innerHTML = "";
    return;
  }
  rail.innerHTML = entries.map((entry) => {
    // 预览文字放进 dataset 交给自绘悬浮卡片，避免原生 title 约 1s 的延迟
    const preview = entry.text.length > 320 ? `${entry.text.slice(0, 320)}…` : entry.text;
    return `<button type="button" class="rail-tick" data-rail-message="${escapeHtml(entry.id)}" data-rail-index="${entry.index}" data-rail-time="${escapeHtml(entry.time)}" data-rail-text="${escapeHtml(preview || "(空输入)")}" aria-label="${escapeHtml(`跳到第 ${entry.index} 条输入`)}"></button>`;
  }).join("");
  rail.hidden = false;
  syncChatRailActive();
}

function showRailTip(tick) {
  const tip = els.railTip;
  if (!tip || !tick) return;
  const area = els.chatArea;
  const rail = els.chatRail;
  if (!area || !rail) return;
  const timeText = String(tick.dataset.railTime || "");
  const total = Number(els.chatRail?.dataset?.count || 0);
  const totalText = total ? ` / 共 ${total} 条` : "";
  tip.innerHTML = `
    <span class="rail-tip-index">第 ${escapeHtml(tick.dataset.railIndex || "?")} 条输入${totalText}${timeText ? ` · ${escapeHtml(timeText)}` : ""}</span>
    <span class="rail-tip-text">${escapeHtml(tick.dataset.railText || "")}</span>
  `;
  tip.hidden = false;
  const areaBox = area.getBoundingClientRect();
  const railBox = rail.getBoundingClientRect();
  const tickBox = tick.getBoundingClientRect();
  const top = Math.min(
    Math.max(tickBox.top + tickBox.height / 2 - tip.offsetHeight / 2 - areaBox.top, 4),
    Math.max(4, areaBox.height - tip.offsetHeight - 4)
  );
  tip.style.left = `${railBox.right - areaBox.left + 6}px`;
  tip.style.top = `${top}px`;
}

function hideRailTip() {
  if (els.railTip) els.railTip.hidden = true;
}

function syncChatRailActive() {
  const rail = els.chatRail;
  if (!rail || rail.hidden || railSyncRaf) return;
  railSyncRaf = window.requestAnimationFrame(() => {
    railSyncRaf = 0;
    const thread = els.chatThread;
    if (!thread) return;
    const box = thread.getBoundingClientRect();
    const center = box.top + box.height / 2;
    let bestId = "";
    let bestDistance = Infinity;
    thread.querySelectorAll("[data-message-id]").forEach((node) => {
      const id = node.getAttribute("data-message-id") || "";
      if (!rail.querySelector(`[data-rail-message="${cssEscape(id)}"]`)) return;
      const rect = node.getBoundingClientRect();
      const distance = Math.abs((rect.top + rect.height / 2) - center);
      if (distance < bestDistance) {
        bestDistance = distance;
        bestId = id;
      }
    });
    rail.querySelectorAll(".rail-tick").forEach((tick) => {
      tick.classList.toggle("active", tick.dataset.railMessage === bestId);
    });
    // 全部记录都在导航条里，把当前条滚进可视区域；鼠标停在导航条上时不抢滚动位置
    const activeTick = rail.querySelector(".rail-tick.active");
    if (activeTick && !(rail.matches && rail.matches(":hover"))) {
      const target = activeTick.offsetTop - rail.clientHeight / 2 + activeTick.offsetHeight / 2;
      rail.scrollTop = Math.max(0, target);
    }
  });
}

function cssEscape(value) {
  if (window.CSS?.escape) return window.CSS.escape(String(value));
  return String(value).replace(/["\\]/g, "\\$&");
}

async function jumpToChatMessage(messageId) {
  if (!messageId || !els.chatThread) return;
  const found = () => [...els.chatThread.querySelectorAll("[data-message-id]")]
    .some((item) => item.getAttribute("data-message-id") === messageId);
  if (!found()) {
    // 该会话可能只渲染了最近消息：先把完整历史拉回来再定位
    await loadCurrentSessionHistory(true).catch(() => {});
  }
  scrollMessageIntoView(messageId);
  requestAnimationFrame(() => {
    const target = [...els.chatThread.querySelectorAll("[data-message-id]")]
      .find((item) => item.getAttribute("data-message-id") === messageId);
    if (!target) return;
    target.classList.remove("chat-jump-highlight");
    // 强制重排以便连续点击同一项时动画能重放
    void target.offsetWidth;
    target.classList.add("chat-jump-highlight");
    window.setTimeout(() => target.classList.remove("chat-jump-highlight"), 1600);
    syncChatRailActive();
  });
}

async function createSession() {
  try {
    await post("/api/sessions", { name: "新对话" });
    closeSessionsOverlays();
    showNotice("新会话已创建", "success");
    await refresh().catch(() => {});
  } catch (error) {
    showNotice(error.message || "创建会话失败", "error");
  }
}

async function loadSession(sessionId) {
  // 立刻给反馈：大会话渲染要花时间，先把对话区压暗，避免看起来"点了没反应"
  setChatSwitching(true);
  try {
    const result = await post(`/api/sessions/${encodeURIComponent(sessionId)}/load`, {});
    const messages = Array.isArray(result?.session?.messages) ? result.session.messages : [];
    fullSessionMessageCache.set(sessionId, messages);
    if (latestState) {
      latestState.current_session = result.session;
      latestState.messages = messages;
      forceNextChatScroll = true;
      render(latestState);
    }
    closeSessionsOverlays();
    showNotice("会话已切换", "info");
  } catch (error) {
    showNotice(error.message || "切换会话失败", "error");
  } finally {
    setChatSwitching(false);
  }
}

function setChatSwitching(switching) {
  const thread = els.chatThread;
  if (!thread) return;
  if (switching) {
    thread.classList.add("is-switching");
    return;
  }
  thread.classList.remove("is-switching");
  thread.classList.add("just-switched");
  window.setTimeout(() => thread.classList.remove("just-switched"), 260);
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

// 行内改名：把名字换成输入框，回车保存、Esc 取消（列表/下拉共用）
function startSessionRowRename(row) {
  if (!row || row.querySelector(".session-edit")) return;
  const sessionId = row.dataset.sessionId || "";
  const nameEl = row.querySelector(".session-name");
  if (!sessionId || !nameEl) return;
  const currentName = nameEl.textContent;
  const input = document.createElement("input");
  input.type = "text";
  input.className = "session-edit";
  input.value = currentName;
  nameEl.hidden = true;
  nameEl.parentNode.insertBefore(input, nameEl);
  input.focus();
  input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const next = input.value.trim();
    input.remove();
    nameEl.hidden = false;
    if (!save || !next || next === currentName) return;
    nameEl.textContent = next;
    await renameSession(sessionId, next);
    await refresh().catch(() => {});
  };
  input.addEventListener("click", (event) => event.stopPropagation());
  input.addEventListener("blur", () => finish(true));
  input.addEventListener("keydown", (event) => {
    event.stopPropagation();
    if (event.key === "Enter") input.blur();
    else if (event.key === "Escape") finish(false);
  });
}

async function renameSession(sessionId, name) {
  try {
    await post(`/api/sessions/${encodeURIComponent(sessionId)}/rename`, { name });
  } catch (error) {
    showNotice(error.message || "重命名失败", "error");
  }
}

async function deleteSession(sessionId) {
  const session = (latestState?.sessions || []).find((s) => s.id === sessionId);
  const name = session?.name || "该会话";
  const confirmed = await confirmDialog({
    title: `删除会话「${name}」`,
    message: "会话消息将从磁盘移除，无法恢复。",
    confirmLabel: "删除",
    danger: true,
  });
  if (!confirmed) return;
  try {
    const result = await post(`/api/sessions/${encodeURIComponent(sessionId)}/delete`, {});
    showNotice("会话已删除", "info");
    if (result?.session) {
      // 删的是当前会话：后端会切到另一个会话，按最新状态重画
      const messages = Array.isArray(result.session.messages) ? result.session.messages : [];
      fullSessionMessageCache.set(result.session.id, messages);
    }
    await refresh().catch(() => {});
  } catch (error) {
    showNotice(error.message || "删除会话失败", "error");
  }
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
    inspect_current_frame: "分析当前画面",
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
    interrupted: "已中断",
  };
  return labels[String(status || "").toLowerCase()] || status || "未知";
}

function eventSourceLabel(source) {
  const key = String(source || "").toLowerCase();
  return EVENT_SOURCE_LABELS[key] || String(source || "系统");
}

function renderEvents(events) {
  if (!els.eventList) return;
  const all = [...(events || [])].slice(-120).reverse();
  if (!all.length) {
    els.eventList.innerHTML = `
      <div class="panel-intro">
        <p>这里记录运行过程中的关键动作与异常：路由怎么选的、调了哪些工具、审批与校验结果、报错原因。</p>
        <p><strong>什么时候看它：</strong>任务没按预期执行、或想知道 Agent 到底做了什么的时候，从上往下找第一条<em>错误</em>或<em>警告</em>。</p>
      </div>
      <div class="empty small">任务开始后这里会出现事件流水。</div>`;
    return;
  }
  const counts = { all: all.length, error: 0, warning: 0, info: 0 };
  all.forEach((event) => {
    const level = String(event.level || "info");
    counts[level] = (counts[level] || 0) + 1;
  });
  const visible = eventLevelFilter === "all" ? all : all.filter((e) => String(e.level || "info") === eventLevelFilter);

  // 连续重复的事件折叠计数，避免"同一句刷屏"淹没真正的问题
  const collapsed = [];
  visible.forEach((event) => {
    const key = `${event.level}|${event.source}|${event.message}`;
    const last = collapsed[collapsed.length - 1];
    if (last && last.key === key) {
      last.count += 1;
      return;
    }
    collapsed.push({ key, event, count: 1 });
  });

  els.eventList.innerHTML = `
    <div class="event-filters">
      ${[["all", "全部"], ["error", "错误"], ["warning", "警告"], ["info", "信息"]]
        .map(([value, label]) => `<button type="button" class="event-filter ${eventLevelFilter === value ? "active" : ""}" data-event-level="${value}">${label} <b>${counts[value] || 0}</b></button>`)
        .join("")}
    </div>
    <div class="event-rows">
      ${collapsed.length ? collapsed.map(({ event, count }) => {
        const level = String(event.level || "info");
        const time = new Date((event.timestamp || 0) * 1000).toLocaleTimeString();
        const hasData = event.data && Object.keys(event.data).length;
        return `
        <article class="event-item ${escapeHtml(level)}" ${hasData ? 'data-event-details role="button" tabindex="0"' : ""}>
          <div class="event-line">
            <span class="event-level">${escapeHtml(EVENT_LEVEL_LABELS[level] || level)}</span>
            <strong>${escapeHtml(eventSourceLabel(event.source))}</strong>
            <span class="event-time">${time}</span>
            ${count > 1 ? `<span class="event-repeat">×${count}</span>` : ""}
          </div>
          <p class="event-message">${escapeHtml(humanizeEventMessage(event.message || "", event.data))}</p>
          ${hasData ? `<pre class="event-data" hidden>${escapeHtml(JSON.stringify(event.data, null, 2))}</pre>` : ""}
        </article>`;
      }).join("") : `<div class="empty small">没有这个级别的事件</div>`}
    </div>`;
}

// 事件文案里常带英文 tool/字段名，翻译成人话；数字与参数保留原文
function humanizeEventMessage(message, data) {
  const text = String(message || "").trim();
  if (!text) return "";
  const toolName = String(data?.tool || data?.name || "");
  if (toolName) return `${humanToolLabel(toolName)}：${text}`;
  return text;
}

function runDurationText(run) {
  const start = Number(run?.started_at || 0);
  const end = Number(run?.finished_at || 0);
  if (!start) return "";
  const seconds = end ? Math.max(0, end - start) : Math.max(0, Date.now() / 1000 - start);
  if (seconds < 60) return `${seconds.toFixed(0)} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`;
}

function renderTaskRuns(taskRuns) {
  if (!els.taskRunList) return;
  const runs = Array.isArray(taskRuns?.recent) ? taskRuns.recent : [];
  if (!runs.length) {
    els.taskRunList.innerHTML = `
      <div class="panel-intro">
        <p>每执行一次任务，这里就留一条复盘记录：目标是什么、走到哪一步、成功还是失败、失败卡在哪。</p>
        <p><strong>什么时候看它：</strong>想确认"上一次那个任务到底做完没有"、或者对比两次同类任务时。</p>
      </div>
      <div class="empty small">还没有任务记录——在输入框切到 Execute 发一条指令就会产生。</div>`;
    return;
  }
  els.taskRunList.innerHTML = runs.slice(0, 8).map((run) => {
    const counters = run.counters || {};
    const title = run.command || run.summary || run.intent || "任务记录";
    const statusKey = String(run.status || "").toLowerCase();
    const statusText = humanRunStatus(run.status);
    const stepsTotal = Number(counters.steps_total || 0);
    const stepsOk = Number(counters.steps_ok || 0);
    const duration = runDurationText(run);
    const meta = [
      stepsTotal ? `${stepsOk}/${stepsTotal} 步完成` : "",
      duration,
      run.route_strategy ? `路由 ${run.route_strategy}` : "",
    ].filter(Boolean).join(" · ");
    const reason = run.failure_reason
      ? `<p class="task-run-failure">卡在这里：${escapeHtml(run.failure_reason)}</p>`
      : "";
    return `
      <article class="compact-item task-run-item ${escapeHtml(statusKey)}" title="${escapeHtml(run.run_id || "")}">
        <div class="memory-item-head">
          <strong>${escapeHtml(String(title).slice(0, 80))}</strong>
          <small class="task-run-status ${escapeHtml(statusKey)}">${escapeHtml(statusText)}</small>
        </div>
        ${meta ? `<p>${escapeHtml(meta)}</p>` : ""}
        ${reason}
      </article>
    `;
  }).join("");
}


function round6(value) {
  return Math.round(Number(value || 0) * 1e6) / 1e6;
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

// 带符号米制差值，用于把 GPS 点投影到航段上
function signedMeters(lat1, lon1, lat2, lon2) {
  const d = haversineMeters(lat1, lon1, lat2, lon2);
  const ref = lat2 !== lat1 ? lat2 - lat1 : lon2 - lon1;
  return ref < 0 ? -d : d;
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

function round1(value) {
  return Math.round(Number(value || 0) * 10) / 10;
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

function buildUserTurn(message) {
  const root = document.createElement("article");
  root.className = "chat-bubble user";
  root.dataset.messageId = message.id || "";
  const content = document.createElement("div");
  content.className = "bubble-content";
  const textEl = document.createElement("div");
  textEl.className = "bubble-text";
  content.appendChild(textEl);
  root.appendChild(content);
  return { root, textEl, kind: "user", lastContent: "" };
}

function updateUserTurn(entry, message) {
  const content = String(message.content || "");
  if (content === entry.lastContent) return;
  entry.lastContent = content;
  entry.textEl.innerHTML = `${renderMessageAttachments(message.attachments || [])}<p>${escapeHtml(content)}</p>`;
}

function buildAgentTurn(message) {
  const root = document.createElement("article");
  root.className = "chat-bubble agent turn";
  root.dataset.messageId = message.id || "";

  const errorPill = document.createElement("div");
  errorPill.className = "error-pill";
  errorPill.textContent = "任务执行失败，详见对话内容";
  errorPill.style.display = "none";

  // 活跃轮次的状态占位行：服务端 assistant 消息先以空内容/占位文本创建，
  // 思考与工具过程要稍后才到；没有这行时气泡会先渲染成完全空白，
  // 操作员只看到"空气泡闪一下再出字"。这里始终给出一个可读状态。
  const statusPill = document.createElement("div");
  statusPill.className = "thinking-pill";
  statusPill.style.display = "none";
  const statusPillDot = document.createElement("span");
  statusPillDot.className = "live-dot";
  const statusPillText = document.createElement("span");
  statusPillText.className = "thinking-pill-text";
  statusPill.appendChild(statusPillDot);
  statusPill.appendChild(statusPillText);

  // 过程折叠块：思考全文 + 工具/校验步骤都在里面。
  // 运行中自动展开（实时看思考与工具追加）；完成后自动收起，只留最终总结；
  // 用户手动切换过（userToggled）则尊重用户选择。
  const procFold = document.createElement("details");
  procFold.className = "proc-fold";
  procFold.style.display = "none";
  const procSummary = document.createElement("summary");
  const procIcon = document.createElement("span");
  procIcon.className = "proc-icon";
  procIcon.textContent = "🧠";
  const procState = document.createElement("span");
  procState.className = "proc-state";
  procState.textContent = "思考过程";
  const procLatest = document.createElement("span");
  procLatest.className = "proc-latest";
  procSummary.appendChild(procIcon);
  procSummary.appendChild(procState);
  procSummary.appendChild(procLatest);

  // 思考折叠块（内层）：默认收起，展开看推理全文——与工具调用区分
  const thinkFold2 = document.createElement("details");
  thinkFold2.className = "think-fold2";
  const thinkSummary = document.createElement("summary");
  const thinkIcon = document.createElement("span");
  thinkIcon.className = "think-icon";
  thinkIcon.textContent = "🧠";
  const thinkState = document.createElement("span");
  thinkState.className = "think-state";
  thinkState.textContent = "思考过程";
  const thinkLatest = document.createElement("span");
  thinkLatest.className = "think-latest";
  thinkSummary.appendChild(thinkIcon);
  thinkSummary.appendChild(thinkState);
  thinkSummary.appendChild(thinkLatest);
  const thinkFull = document.createElement("pre");
  thinkFull.className = "think-full";
  thinkFold2.appendChild(thinkSummary);
  thinkFold2.appendChild(thinkFull);

  const toolLines = document.createElement("div");
  toolLines.className = "tool-lines";

  // 时间线：按发生顺序交错排列「模型思考 → 工具/技能调用 → 下一轮思考」，
  // plan-execute 与 ReAct 用同一套结构（ReAct 里思考块会多次出现）。
  const timeline = document.createElement("div");
  timeline.className = "proc-timeline";

  // 计划摘要块：任务理解 + 步骤序列（与"模型思考"分开显示）
  const planBlock = document.createElement("div");
  planBlock.className = "plan-block";
  planBlock.style.display = "none";
  const planHead = document.createElement("div");
  planHead.className = "plan-head";
  planHead.textContent = "📋 执行计划";
  const planBody = document.createElement("pre");
  planBody.className = "plan-body";
  planBlock.appendChild(planHead);
  planBlock.appendChild(planBody);

  procFold.appendChild(procSummary);
  procFold.appendChild(timeline);   // 计划块在时间线内按位置插入
  procFold.appendChild(toolLines);  // 兼容旧引用，实际渲染走 timeline

  const answerBody = document.createElement("div");
  answerBody.className = "answer-body";

  const entry = {
    root,
    kind: "agent",
    errorPill,
    statusPill,
    statusPillText,
    lastPillText: "",
    lastPillShown: false,
    lastEmptyTurn: false,
    procFold,
    procState,
    procLatest,
    thinkFold2,
    thinkState,
    thinkLatest,
    thinkFull,
    planBlock,
    planBody,
    toolLines,
    timeline,
    renderedTimeline: 0,
    answerBody,
    renderedTrace: 0,
    userToggled: false,
    thinkUserToggled: false,
    _programmatic: false,
    _programmaticThink: false,
    startedAt: Date.now() / 1000,
    lastAnswer: "",
    lastReasoning: "",
    lastRunning: undefined,
    lastStatus: "",
  };
  procFold.addEventListener("toggle", () => {
    if (entry._programmatic) {
      entry._programmatic = false;
      return;
    }
    entry.userToggled = true;
  });
  thinkFold2.addEventListener("toggle", () => {
    if (entry._programmaticThink) {
      entry._programmaticThink = false;
      return;
    }
    entry.thinkUserToggled = true;
  });

  root.appendChild(errorPill);
  root.appendChild(statusPill);
  root.appendChild(procFold);
  root.appendChild(answerBody);
  return entry;
}

function latestThinkLine(text) {
  const t = String(text || "").trimEnd();
  const n = t.lastIndexOf("\n");
  const line = n === -1 ? t : t.slice(n + 1);
  return line.length > 90 ? line.slice(-90) : line;
}

function firstThinkLine(text) {
  const t = String(text || "").trim();
  const n = t.indexOf("\n");
  return (n === -1 ? t : t.slice(0, n)).slice(0, 90);
}

// 条目分类：技能调用(kind=skill 或 skill:*) / 工具 / 校验 / 记忆 / 系统说明 / 模型思考
// 后端现在给技能激活单独打 kind="skill"；旧运行（以及运行中还没收到 kind 的
// 那一行）只有 tool="skill:<name>"，所以前缀兜底继续保留。
function nodeCategory(item) {
  const tool = String(item?.tool || "");
  if (tool.startsWith("skill:")) return "skill";
  const kind = normalizeProcessKind(item);
  if (kind === "skill") return "skill";
  if (kind === "plan_step" || kind === "tool") return "tool";
  if (kind === "verify") return "verify";
  if (kind === "memory") return "memory";
  if (kind === "system" || kind === "plan") return "system";
  return "reasoning";
}

// 后端在模型没给出思考时用 "Call <tool>" 占位（llm.py reason=...），
// 那是合成标签不是推理，不能当模型思考展示
function isSyntheticToolCallLabel(text) {
  return /^Call [A-Za-z0-9_.]+$/.test(String(text || "").trim());
}

// 时间线行的稳定 key：分类/工具名/标题 + 序号，重建时用它恢复折叠状态
function timelineRowKey(item, idx) {
  const cat = nodeCategory(item);
  return `${idx}:${cat}:${String(item.tool || item.title || "")}`;
}

// 规划期临时思考块的判据：时间线里还没有真实条目（思考/工具/技能/校验/记忆）
// 时才显示 details.reasoning_text。规划刚开始时时间线上只有一条"理解指令"
// 阶段说明——它没有 kind，会被 nodeCategory 兜底归成 reasoning——所以"真实行"
// 按有无 kind 判断，不能只看分类，否则规划期永远插不进临时块。一旦出现真实行
// （哪怕已是 completed/failed），这份整轮累计文本就永远不能再插，否则运行中
// 它会重新跑到时间线末尾（工具行之后），看起来就是"流式输出位置不对"。
function shouldShowPlanPhaseReasoning(items) {
  return !(Array.isArray(items) ? items : []).some((item) => {
    const cat = nodeCategory(item);
    if (cat === "system") return false; // 阶段说明不是真实条目
    if (cat !== "reasoning") return true; // 工具/技能/校验/记忆都是真实行
    return Boolean(String(item?.kind || "").trim()); // 真实思考行都带 kind
  });
}

function categoryLabel(cat) {
  if (cat === "tool") return "工具";
  if (cat === "skill") return "技能调用";
  if (cat === "verify") return "校验";
  if (cat === "memory") return "记忆";
  if (cat === "system") return "系统";
  return "模型思考";
}

function systemNode(item) {
  const row = document.createElement("div");
  row.className = "tl-note";
  row.textContent = `${humanThoughtTitle(item.title || "说明")}：${humanThoughtBody(item.body || "", "")}`;
  return row;
}

// ── 可展开行：一行摘要 + 详情 ──────────────────────────────────────────
// 展开器必须是真正可聚焦的控件：用原生 <details>/<summary>（Tab 可达、
// 回车/空格切换、读屏播报展开态），不是 div onclick。只有这一行之外还有
// 内容（参数 / 返回 / 技能指导全文）时才包 details——内容已经全在这行里的
// 不给假展开器。
function foldChevron() {
  const chev = document.createElement("i");
  chev.className = "tl-fold-chev"; // 装饰：展开态由原生 summary 播报
  chev.setAttribute("aria-hidden", "true");
  return chev;
}

function expandableRow(row, detailNodes, ariaLabel) {
  if (!detailNodes.length) return row;
  const fold = document.createElement("details");
  fold.className = "tl-fold";
  const summary = document.createElement("summary");
  summary.className = "tl-fold-head";
  summary.setAttribute("aria-label", ariaLabel);
  summary.title = ariaLabel;
  row.appendChild(foldChevron());
  summary.appendChild(row);
  fold.appendChild(summary);
  detailNodes.forEach((node) => fold.appendChild(node));
  return fold;
}

// 展开态恢复：工具/技能行的展开器就是节点自身，思考行的展开器在节点内部
function findRowFold(node) {
  if (!node) return null;
  if (node.tagName === "DETAILS") return node;
  return node.querySelector("details");
}

// 详情小节：小标题 + 等宽正文（请求参数 / 返回内容）
function detailSection(parent, heading, text) {
  const head = document.createElement("div");
  head.className = "tl-one-section";
  head.textContent = heading;
  const pre = document.createElement("pre");
  pre.className = "tl-collapse-full";
  pre.textContent = text;
  parent.appendChild(head);
  parent.appendChild(pre);
}

// 思考全文比一行摘要多才值得展开：有换行，或长到单行肯定被省略号截断。
// （工具/技能/校验行不再看摘要长短：参数与返回本来就在这一行之外，见各自节点函数。）
function textNeedsDetail(text) {
  const value = String(text || "");
  return value.length > 90 || value.includes("\n");
}

// 工具/技能/校验：默认压缩成一行（超出省略），整行可点开看完整参数与返回；
// 只有参数、返回两样都空的行才是真正的纯一行。
function toolLineNode(item) {
  const status = item.status || "completed";
  const cat = nodeCategory(item);
  const rawTool = String(item.tool || "");
  const label = rawTool ? humanToolLabel(rawTool, item.title) : humanThoughtTitle(item.title || "");
  const nameText = cat === "skill" && rawTool ? rawTool.replace(/^skill:/, "") : (rawTool || label);
  const paramsText = item.params && Object.keys(item.params).length ? compactJson(item.params, 2000) : "";
  const resultText = humanThoughtBody(item.body || "", item.tool || "");
  const kindLabel = cat === "skill" ? "技能调用" : cat === "verify" ? "校验" : "工具调用";

  // 一行摘要：图标 + 标题 + 工具名 + 结果（优先）/参数（次要）
  const row = document.createElement("div");
  row.className = `tl-one-line ${status} cat-${cat}`;
  const icon = document.createElement("i");
  icon.className = "tl-node-icon";
  icon.textContent = cat === "skill" ? "🧩" : cat === "verify" ? "📋" : "🛠";
  icon.setAttribute("aria-hidden", "true");
  row.appendChild(icon);
  const kind = document.createElement("span");
  kind.className = "tl-node-label";
  kind.textContent = kindLabel;
  row.appendChild(kind);
  const name = document.createElement("code");
  name.className = "tool-name";
  name.textContent = nameText;
  row.appendChild(name);

  // 详情就是 process_trace 里已有的字段：params + 格式化后的 body
  const detailBox = document.createElement("div");
  detailBox.className = "tl-one-body";
  let detailCount = 0;
  if (paramsText) {
    detailSection(detailBox, "请求参数", paramsText);
    detailCount += 1;
  }
  if (resultText) {
    detailSection(detailBox, "返回内容", resultText);
    detailCount += 1;
  }
  const detailNodes = detailCount ? [detailBox] : [];

  if (status === "running") {
    const spin = document.createElement("i");
    spin.className = "tl-spin";
    row.appendChild(spin);
    const t = document.createElement("span");
    t.className = "tl-line-note";
    t.textContent = "执行中…";
    row.appendChild(t);
    // 运行中也能先看已拿到的参数/返回——那正是这一行之外的内容
    if (!detailNodes.length) return row;
    return expandableRow(row, detailNodes, `${kindLabel} ${nameText}：展开查看完整参数与返回`);
  }

  const brief = document.createElement("span");
  brief.className = "tl-line-brief";
  const briefText = resultText || paramsText || "";
  brief.textContent = briefText;
  row.appendChild(brief);

  // 展开器不再看摘要长短：摘要永远是一行省略文本，而参数与返回都在这行之外，
  // 短摘要（如 drone_get_status → ok）同样点得开；参数、返回两样都空才是
  // 真正的纯一行，那种行不给假展开器。
  if (!detailNodes.length) return row;
  return expandableRow(row, detailNodes, `${kindLabel} ${nameText}：展开查看完整参数与返回`);
}

// 技能激活：与"模型思考/工具调用"同级的一条——图标 + "技能调用" + 技能名 +
// 指导摘要；整行可点开，展开后完整技能指导内联显示在下方（不再另起一行
// "点击展开全文"，重建时靠 data-tl-row 恢复展开态）。
// 状态只沿用 tool 行那套 running / completed / failed，不新增状态。
function skillNode(item) {
  const status = item.status || "completed";
  const rawTool = String(item.tool || "");
  const nameText = rawTool || humanThoughtTitle(item.title || "") || "技能";
  const guidance = String(item.body || "").trim();
  const paramsText = item.params && Object.keys(item.params).length ? compactJson(item.params, 2000) : "";
  const node = document.createElement("div");
  node.className = `tl-node tl-skill${status === "running" ? " running" : ""}`;
  const row = document.createElement("div");
  row.className = `tl-one-line ${status} cat-skill`;
  const icon = document.createElement("i");
  icon.className = "tl-node-icon";
  icon.textContent = "🧩";
  icon.setAttribute("aria-hidden", "true");
  row.appendChild(icon);
  const kind = document.createElement("span");
  kind.className = "tl-node-label";
  kind.textContent = "技能调用";
  row.appendChild(kind);
  const name = document.createElement("code");
  name.className = "tool-name";
  name.textContent = nameText;
  row.appendChild(name);
  if (status === "running") {
    const spin = document.createElement("i");
    spin.className = "tl-spin";
    row.appendChild(spin);
    const note = document.createElement("span");
    note.className = "tl-line-note";
    note.textContent = "取回技能指导…";
    row.appendChild(note);
    node.appendChild(row);
    return node;
  }
  const brief = document.createElement("span");
  brief.className = "tl-line-brief";
  brief.textContent = guidance ? guidance.replace(/\s+/g, " ") : "（无返回内容）";
  row.appendChild(brief);
  // 详情里放的是完整技能指导（backend 把全文放在 body 里，可能很长）与激活
  // 参数：摘要只是一行省略文本，操作员必须能展开看到全文；两者都空才是纯一行。
  const detailNodes = [];
  if (paramsText) {
    const box = document.createElement("div");
    box.className = "tl-one-body";
    detailSection(box, "请求参数", paramsText);
    detailNodes.push(box);
  }
  if (guidance) {
    const full = document.createElement("pre");
    full.className = "tl-skill-full";
    full.textContent = guidance;
    detailNodes.push(full);
  }
  if (!detailNodes.length) {
    node.appendChild(row);
    return node;
  }
  node.appendChild(expandableRow(row, detailNodes, `技能调用 ${nameText}：展开查看完整技能指导`));
  return node;
}

// 模型思考行：与下方工具/技能/校验行同一套外观——图标 + 加粗标签"模型思考" +
// 可选阶段名 + 正文预览（单行省略），整行可点开，展开后全文内联显示在下方——
// 没有单独的"▸ 点击展开全文"行，也没有套边框的可滚动卡片；ReAct 多轮思考与
// plan-execute 的思考共用这一种外观。
// 头部恒为一行：正文一律走 .tl-think-preview（CSS nowrap + ellipsis），绝不把
// 正文节点塞进这一行的 flex 容器里——否则长文本会在标题行里换行，把
// "图标 + 模型思考 + 阶段名 + 正文"挤成一团多层重叠的头部。
// 运行中的行同样只显示这一行预览（+ 运行指示符），默认收起；点开后在下方就地
// 增长全文——长规划文本不再自动铺满时间线，操作员想看才展开。
// 运行态按条目自身的 status 判定（不是"是否最后一个流式行"）：状态由后端收尾时
// 时间线按签名重建，转圈/光标不会残留。
function timelineThinkNode(item) {
  const isRunning = (item.status || "completed") === "running";
  const text = String(item.body || "").trim();
  const titleText = item.title && item.title !== "模型思考" ? String(item.title) : "";
  const node = document.createElement("div");
  node.className = `tl-node tl-reasoning${isRunning ? " running" : ""}`;
  const row = document.createElement("div");
  row.className = "tl-think-head";
  const icon = document.createElement("i");
  icon.className = "tl-node-icon";
  icon.textContent = "🧠";
  icon.setAttribute("aria-hidden", "true");
  row.appendChild(icon);
  // 标签：restyle 时被漏掉，操作员只能看到图标 + 裸正文，认不出这是哪一类行
  const kind = document.createElement("span");
  kind.className = "tl-node-label";
  kind.textContent = "模型思考";
  row.appendChild(kind);
  if (titleText) {
    const title = document.createElement("span");
    title.className = "tl-node-title"; // 阶段名：CSS nowrap + 省略，不许换行压正文
    title.textContent = `· ${titleText}`;
    row.appendChild(title);
  }
  const preview = document.createElement("span");
  preview.className = "tl-think-preview"; // 单行省略：CSS nowrap + ellipsis
  const previewText = firstThinkLine(text) || (isRunning ? "思考中…" : "（无内容）");
  preview.textContent = previewText;
  row.appendChild(preview);
  // 运行指示符：独立元素（不是预览的 ::after），缩略成省略号时也不会被裁掉；
  // 仍是原来那个闪烁的 ▍ 光标，样式见 styles.css 的 .tl-think-caret。
  if (isRunning) {
    const caret = document.createElement("i");
    caret.className = "tl-think-caret";
    caret.setAttribute("aria-hidden", "true");
    row.appendChild(caret);
  }
  // 全文（展开后在下方内联显示）：运行中同样生成——折叠时按格式就地更新，
  // 展开时"到目前为止的全部文本"原地增长，重建不重放动画、不丢展开态。
  // 已完成的短思考本来就看全了，不给假展开器（与工具/技能行同一约定）。
  const full = document.createElement("pre");
  full.className = "tl-think-full";
  full.textContent = text;
  const detailNodes = isRunning || textNeedsDetail(text) ? [full] : [];
  node.appendChild(expandableRow(row, detailNodes, `模型思考：${previewText}（展开查看全文）`));
  return node;
}

// 活跃轮次的状态文案：优先用 run 的 phase/status（更细），退回消息自身字段。
// 与已废弃的 renderChatMessage 里的 thinking-pill 文案同源（humanStatus）。
function agentStatusPillText(message, linkedRun = null) {
  const status = linkedRun?.status || message?.status || "";
  const phase = linkedRun?.phase || message?.details?.phase || "";
  const mode = linkedRun?.mode || message?.details?.mode || "";
  return humanStatus(status, phase, mode);
}

function updateAgentTurn(entry, message, run, llm) {
  // 已完成消息的快速路径：状态与内容都没变就不再逐区块更新
  const fastSkip =
    entry.lastStatus === message.status &&
    entry.lastAnswer === String(message.content || "") &&
    entry.lastReasoning === String(message.details?.reasoning_text || "") &&
    entry.renderedTrace > 0;
  if (fastSkip && !["running", "responding", "queued"].includes(message.status)) return;
  const details = message.details || {};
  const reasoning = String(details.reasoning_text || "");
  const running = ["running", "responding", "queued"].includes(message.status);
  const isError = message.status === "error";

  // 错误徽标
  entry.errorPill.style.display = isError ? "" : "none";

  // 过程条目：从 run（运行中）或消息 details（结束后）取，按时间线渲染
  const linkedRun = run && message.run_id && run.run_id === message.run_id ? run : null;
  const processTrace = Array.isArray(linkedRun?.process_trace) && linkedRun.process_trace.length
    ? linkedRun.process_trace
    : (Array.isArray(details.process_trace) ? details.process_trace : []);
  // 时间线按发生顺序渲染：模型思考 / 工具 / 技能 / 校验 交错出现。
  // plan-execute 呈现为「思考→计划→步骤序列」，ReAct 自然呈现为
  // 「思考→工具→结果→思考→工具…」——同一套结构，无需分模式。
  const timelineItems = processTrace.filter((item) => {
    if (item.tool === "memory_store") return false;
    // kind=plan 的工具清单与上方"执行计划"块重复，不再重复展示
    if (normalizeProcessKind(item) === "plan") return false;
    const cat = nodeCategory(item);
    if (cat === "reasoning") {
      const body = String(item.body || "").trim();
      return Boolean(body) && !isSyntheticToolCallLabel(body);
    }
    if (cat === "system") {
      return Boolean(String(item.body || "").trim());
    }
    return Boolean(item.tool || humanThoughtTitle(item.title || ""));
  });
  const planSummaryText = String(details.plan_summary || "").trim();
  // 规划阶段（LLM 正在生成计划）process_trace 里只有一条没有 kind 的"理解指令"
  // 占位行：这时把消息 details 里的流式推理就地写进那条已有思考行——写进去的
  // 是同一行，绝不追加第二行，否则会出现两条"模型思考"（一条占位、一条流式），
  // 流式文本还落在第二条里。只有时间线上一条思考行都没有时才新建。
  // 判据仍是"时间线里还没有真实条目"：真实思考行一旦出现（哪怕已经 completed），
  // 这份整轮累计文本就永远不能再写进去，否则运行中它会重新跑到时间线末尾
  // （工具行之后），看起来就是"流式输出位置不对"；ReAct 的每轮正文由 process_trace
  // 条目自己流式更新，累计文本写进去会让新一轮显示整段历史思考。
  // 规划期本来就有一条"理解指令"的阶段说明，所以不能用"时间线为空"当判据。
  if (running && reasoning.trim() && shouldShowPlanPhaseReasoning(timelineItems)) {
    let thinkIdx = -1;
    for (let i = timelineItems.length - 1; i >= 0; i -= 1) {
      if (nodeCategory(timelineItems[i]) === "reasoning") {
        thinkIdx = i;
        break;
      }
    }
    if (thinkIdx < 0) {
      timelineItems.push({
        title: "模型思考",
        body: reasoning,
        status: "running",
        tool: "",
        params: {},
        kind: "reasoning",
      });
    } else if (timelineItems[thinkIdx].status === "running") {
      // 就地更新：只换正文，title/status/行 key 都不动，签名不变、不触发重建，
      // 流式正文由下面的就地写入按 .tl-think-preview/.tl-think-full 更新。
      timelineItems[thinkIdx] = { ...timelineItems[thinkIdx], body: reasoning };
    }
    // 已有思考行但已收尾：不新增、不覆写（本轮思考已经显示过了）。
  }
  const hasProcess = Boolean(reasoning) || timelineItems.length > 0 || Boolean(planSummaryText);
  // 计划文本必须在时间线重建之前写入：重建时按它决定是否插入计划块，
  // 且插入位置在"思考之后、第一个工具之前"（先有思考才有计划）。
  if (planSummaryText && entry.planBody.textContent !== planSummaryText) {
    entry.planBody.textContent = planSummaryText;
    entry.planBlock.classList.remove("flash-in");
    void entry.planBlock.offsetWidth; // 重放进入动画
    entry.planBlock.classList.add("flash-in");
  }
  if (planSummaryText) entry.planBlock.style.display = "";
  // 运行中思考正文由 process_trace 条目自身的 body 流式更新（规划期则由上面
  // 把那一条占位行改写为本轮流式文本）；除了规划期这一条已收尾的行，不可再用
  // 累计的 reasoning_text 覆写 ReAct 的最后一条，否则新一轮思考块会显示整段
  // 历史思考。
  // 内容签名变化就重建时间线：条目状态会从 running → completed/failed，
  // 只做增量追加会让已渲染的行永远停在"转圈"。
  // 注意：不计入"正在流式的那个思考块的正文"，否则每个 token 都会重建、
  // 打字机动画被反复打断；流式正文在重建之外就地更新。
  // 活跃行取"最后一个 running 思考"（新一轮追加在末尾）：上一轮的残留
  // running 行还在时，findIndex 取第一条会把流式文本全部写进旧行。
  let liveIdx = -1;
  if (running) {
    for (let i = timelineItems.length - 1; i >= 0; i -= 1) {
      if (nodeCategory(timelineItems[i]) === "reasoning" && timelineItems[i].status === "running") {
        liveIdx = i;
        break;
      }
    }
  }
  const liveKey = liveIdx >= 0 ? timelineRowKey(timelineItems[liveIdx], liveIdx) : "";
  let sig = "";
  try {
    sig = JSON.stringify([
      liveKey, // 活跃行身份入签名：新增/切换 running 行时重建一次，同一行涨文本不重建
      timelineItems.map((i, idx) => [
        i.tool,
        i.status,
        i.params,
        i.title,
        idx === liveIdx ? String(i.body || "").length > 0 : String(i.body || ""),
      ]),
    ]);
  } catch (e) {
    sig = String(timelineItems.length) + "|" + (running ? "1" : "0") + "|" + liveKey;
  }
  if (sig !== entry.timelineSig) {
    entry.timelineSig = sig;
    // 重建会整体替换节点：先按行 key 记下已展开的折叠块，重建后恢复，
    // 否则流式期间每次重建都会把用户展开的思考块收起。
    const openRows = new Set(
      Array.from(entry.timeline.querySelectorAll("details[open]"))
        .map((d) => d.closest("[data-tl-row]")?.dataset.tlRow)
        .filter(Boolean)
    );
    entry.timeline.textContent = "";
    // 计划块插到"思考之后、第一个工具之前"——先有 LLM 思考才有计划，
    // 不能顶在最上面。
    let planInserted = false;
    timelineItems.forEach((item, idx) => {
      const cat = nodeCategory(item);
      const toolLike = cat === "tool" || cat === "skill" || cat === "verify";
      if (!planInserted && toolLike && entry.planBody.textContent) {
        entry.timeline.appendChild(entry.planBlock);
        planInserted = true;
      }
      let node;
      // 思考行用 ui-chat.js 自己的渲染（图标紧跟正文、展开内联）：ui-composer.js
      // 里的旧 reasoningNode 是"标题行 + 单独一行点击展开全文"，样式已被替换。
      // 运行态由条目自身的 status 决定，不再传"是否活跃行"。
      if (cat === "reasoning") node = timelineThinkNode(item);
      else if (cat === "skill") node = skillNode(item);
      else if (cat === "system") node = systemNode(item);
      else node = toolLineNode(item);
      const key = timelineRowKey(item, idx);
      node.dataset.tlRow = key;
      if (openRows.has(key)) {
        const fold = findRowFold(node);
        if (fold) fold.open = true;
      }
      entry.timeline.appendChild(node);
    });
    if (!planInserted && entry.planBody.textContent) entry.timeline.appendChild(entry.planBlock);
  } else if (liveIdx >= 0) {
    // 结构未变：只把流式文本就地写进活跃思考行（打字机效果），不重建节点，
    // 因此用户展开的折叠块保持展开、也不重放进入动画。
    // 按行 key 定位而不是 .running 类，避免命中残留的旧 running 行。
    const liveNode = Array.from(entry.timeline.children).find((n) => n.dataset.tlRow === liveKey);
    if (liveNode) {
      const text = String(timelineItems[liveIdx].body || "");
      // 头部预览（恒一行，省略号截断）与展开后的全文各自就地更新：折叠时只动
      // 这一行预览，展开时全文在下方原地增长。
      const preview = liveNode.querySelector(".tl-think-preview");
      if (preview) {
        const line = firstThinkLine(text) || "思考中…";
        if (preview.textContent !== line) preview.textContent = line;
      }
      const full = liveNode.querySelector(".tl-think-full");
      if (full && full.textContent !== text) full.textContent = text;
    }
  }
  entry.renderedTrace = timelineItems.length;
  entry.renderedTimeline = timelineItems.length;

  // 外层过程折叠块：思考 + 工具/校验步骤全部收在里面。
  // 运行中自动展开（内层思考块同步展开、标题滚动最新思考句）；
  // 完成后自动收起，只留最终总结；用户手动切换过则尊重用户选择。
  if (hasProcess) {
    entry.procFold.style.display = "";
    const dur = Math.max(1, Math.round(Date.now() / 1000 - entry.startedAt));
    if (running) {
      entry.procState.textContent = `思考与执行中 · 约 ${dur}s`;
      if (!entry.procFold.open && !entry.userToggled) {
        entry._programmatic = true;
        entry.procFold.open = true;
      }
    } else {
      entry.procState.textContent = `思考与执行过程 · 约 ${dur}s`;
      entry.procLatest.textContent = "";
      if (entry.procFold.open && !entry.userToggled) {
        entry._programmatic = true;
        entry.procFold.open = false; // 完成后折叠全部过程，只留最终总结
      }
    }

    // 思考正文只在时间线里显示（运行中的最后一块就地打字机增长）。标题行
    // 不再滚动"最新一句"：思考文字出现在折叠标题里看起来像"输出位置不对"，
    // 而且它是整句替换，视觉上就是一句一句蹦，不是流式输出。
    entry.procLatest.textContent = "";
    entry.procLatest.scrollLeft = 0;
  } else {
    entry.procFold.style.display = "none";
    entry.planBlock.style.display = "none";
  }

  // 正文：平滑分批释放（smoothShownContent）；运行中推理不占正文，
  // 完成后填入 LLM 总结
  const text = smoothShownContent(message).trim();
  if (text !== entry.lastAnswer) {
    entry.lastAnswer = text;
    entry.answerBody.innerHTML = text ? renderMarkdown(text) : "";
    entry.answerBody.style.display = text ? "" : "none";
  }

  // 活跃且尚无任何可见内容（过程/正文）时，显示一行状态占位而不是留空白。
  // 覆盖两个空窗：服务端 message_create 到首条 delta 之间，以及平滑流式
  // 尚未释放第一批正文期间。正文/过程一出现立即收起（不重建节点，只切
  // display 与文本，避免闪烁）。
  const showPill = running && !isError && !text && !hasProcess;
  if (showPill) {
    const pillText = agentStatusPillText(message, linkedRun);
    if (entry.lastPillText !== pillText) {
      entry.lastPillText = pillText;
      entry.statusPillText.textContent = pillText;
    }
  }
  if (entry.lastPillShown !== showPill) {
    entry.lastPillShown = showPill;
    entry.statusPill.style.display = showPill ? "" : "none";
  }

  entry.root.classList.toggle("error", isError);
  // 与 renderChatMessage 的既有约定一致：非活跃且完全无内容的助手消息不留
  // 孤立空气泡（真正有内容的完成/错误消息不受影响）。
  const emptyTurn = !running && !text && !hasProcess && !isError;
  if (entry.lastEmptyTurn !== emptyTurn) {
    entry.lastEmptyTurn = emptyTurn;
    entry.root.style.display = emptyTurn ? "none" : "";
  }
  entry.lastStatus = message.status;
}

// 增量渲染判据：消息对象被 SSE upsert 替换/就地修改（bindPendingRunId 写
// run_id、updateMessageContent 追加正文）后 key 必变；同一份状态被重复渲染
// （提交时乐观追加、refresh 与 SSE 重叠触发）时 key 不变，整条消息的 DOM
// 更新可以直接跳过。details 只取渲染真正读取的字段（含 process_trace 每个
// 条目的状态与正文长度），不做深比较。
function messageRenderKey(message) {
  if (!message) return "";
  const details = message.details || {};
  const trace = Array.isArray(details.process_trace) ? details.process_trace : [];
  return [
    message.id || "",
    message.role || "",
    message.status || "",
    message.run_id || "",
    String(message.content || "").length,
    message.updated_at || message.created_at || 0,
    details.phase || "",
    details.mode || "",
    String(details.reasoning_text || "").length,
    String(details.plan_summary || "").length,
    Array.isArray(message.attachments) ? message.attachments.length : 0,
    trace.map((item) => `${item?.status || ""}:${String(item?.body || "").length}`).join(","),
  ].join("|");
}

function renderChat(messages, run, llm) {
  // Initial page open (empty thread) jumps to the newest message instead of
  // parking on the first line.
  if (!els.chatThread || !els.chatThread.firstChild) forceNextChatScroll = true;
  if (els.chatThread && !chatFollowBound) {
    chatFollowBound = true;
    els.chatThread.addEventListener("scroll", () => {
      syncChatRailActive();
      // 忽略我们自己发起的贴底滚动，只在用户真正滚动时更新跟随意图。
      if (Date.now() - chatProgrammaticScrollAt < 250) return;
      chatAutoFollow = shouldStickToChatBottom();
    }, { passive: true });
    // 内容增长（思考块展开、逐条过程行、图片加载后的高度变化）不会触发
    // 我们自己的滚动调用，这里用 MutationObserver 在 DOM 变化后重新贴底，
    // 保证输出始终跟着走，而不是要用户手动下滑。
    if (typeof MutationObserver !== "undefined") {
      chatContentObserver = new MutationObserver(() => {
        if (!chatAutoFollow) return;
        pinChatToBottom();
        window.requestAnimationFrame(() => {
          if (chatAutoFollow) pinChatToBottom();
        });
      });
      chatContentObserver.observe(els.chatThread, { childList: true, subtree: true, characterData: true });
    }
  }
  // 关键：在更新 DOM 之前记录是否停在底部，否则新增内容本身就会把距离拉大。
  const stickBefore = forceNextChatScroll || chatAutoFollow;
  const serverMessages = Array.isArray(messages) ? messages : [];
  reconcilePendingMessages(serverMessages);
  const list = [...serverMessages, ...localPendingMessages];

  if (!list.length) {
    els.chatThread.innerHTML = `<div class="chat-empty">开始一段新的对话</div>`;
    turnNodes.clear();
    return;
  }
  if (els.chatThread.firstElementChild?.classList?.contains("chat-empty")) {
    els.chatThread.innerHTML = "";
  }

  const liveIds = new Set();
  const orderedRoots = [];
  for (const message of list) {
    const id = message.id || `idx_${message.role}_${list.indexOf(message)}`;
    liveIds.add(id);
    let entry = turnNodes.get(id);
    if (!entry) {
      entry = message.role === "user" ? buildUserTurn(message) : buildAgentTurn(message);
      entry.root.dataset.messageId = message.id || id;
      turnNodes.set(id, entry);
      els.chatThread.appendChild(entry.root);
    }
    orderedRoots.push(entry.root);
    // 增量纪律：消息 key 与 run 引用都没变时跳过该条的 DOM 更新。乐观追加
    // 一次只需 O(N) 次字符串比较，不再对整份消息列表做 DOM 写入。
    const renderKey = messageRenderKey(message);
    if (entry.lastRenderKey === renderKey && entry.lastRenderRun === run) continue;
    entry.lastRenderKey = renderKey;
    entry.lastRenderRun = run;
    if (entry.kind === "agent") updateAgentTurn(entry, message, run, llm);
    else updateUserTurn(entry, message);
  }
  for (const [id, entry] of [...turnNodes]) {
    if (!liveIds.has(id)) {
      entry.root.remove();
      turnNodes.delete(id);
    }
  }
  reconcileChatOrder(orderedRoots);

  renderChatRail(list);
  const scrollTargetId = pendingScrollTargetId;
  const shouldScroll = !scrollTargetId && stickBefore;
  if (scrollTargetId) scrollMessageIntoView(scrollTargetId);
  else if (shouldScroll) {
    chatAutoFollow = true;
    scrollChatToEnd();
  }
  forceNextChatScroll = false;
}

function scheduleChatRender() {
  if (chatRenderRafId) return;
  chatRenderRafId = window.requestAnimationFrame(() => {
    chatRenderRafId = 0;
    renderChat(latestState.messages || [], latestState.current_run, latestState.llm || {});
    syncCommandSubmitState();
  });
}

function reconcilePendingMessages(serverMessages = []) {
  if (!localPendingMessages.length) return;
  localPendingMessages = localPendingMessages.filter((pending) => {
    if (pending.role === "user") {
      return !serverMessages.some((message) => serverConfirmsPendingUser(message, pending));
    }
    if (pending.role === "assistant") {
      const pendingMode = pending.details?.mode || "";
      if (pending.run_id) {
        return !serverMessages.some((message) => message.role === "assistant" && message.run_id === pending.run_id);
      }
      return !serverMessages.some((message) =>
        message.role === "assistant" && (message.details?.mode || "") === pendingMode && message.status === "running"
      );
    }
    return true;
  });
}

// DOM 顺序必须跟消息数组顺序一致。
// 这些 turn 节点是增量复用的：只在"新建"时 appendChild，之后就不再动位置。
// 于是只要中途顺序发生变化（合并完整历史、服务端修正顺序、切会话后回填），
// 旧消息就会停在原来的 DOM 位置——表现就是"最新那条输入却显示在最前面/最后面"。
// 这里按目标顺序核对一遍，只在错位时搬动节点，顺序正确时不触碰 DOM。
function reconcileChatOrder(orderedRoots) {
  const thread = els.chatThread;
  if (!thread) return;
  let cursor = thread.firstElementChild;
  for (const want of orderedRoots) {
    if (!want || want.parentNode !== thread) continue;
    if (cursor === want) {
      cursor = cursor.nextElementSibling;
      continue;
    }
    thread.insertBefore(want, cursor);
  }
}

function serverConfirmsPendingUser(message, pending) {
  if (!message || !pending || message.role !== "user" || pending.role !== "user") return false;
  if (String(message.content || "") !== String(pending.content || "")) return false;
  const pendingCreated = Number(pending.pending_created_at || pending.created_at || 0);
  const serverCreated = Number(message.created_at || message.updated_at || 0);
  if (!pendingCreated || !serverCreated) return false;
  return serverCreated >= pendingCreated - 0.001;
}

function removePendingForServerMessage(message) {
  if (!message || !localPendingMessages.length) return;
  if (message.role === "user") {
    const index = localPendingMessages.findIndex((pending) => serverConfirmsPendingUser(message, pending));
    if (index >= 0) {
      pendingScrollTargetId = message.id || pendingScrollTargetId;
      localPendingMessages.splice(index, 1);
    }
    return;
  }
  if (message.role === "assistant") {
    const mode = message.details?.mode || "";
    localPendingMessages = localPendingMessages.filter((pending) => {
      if (pending.role !== "assistant") return true;
      if (message.run_id && pending.run_id === message.run_id) return false;
      return pending.run_id || (pending.details?.mode || "") !== mode;
    });
  }
}

function renderChatMessage(message, run, llm) {
  const role = message.role === "user" ? "user" : "agent";
  if (role === "user") {
    return `
      <article class="chat-bubble user" data-message-id="${escapeHtml(message.id || "")}">
        <div class="bubble-content">
          <div class="bubble-text">
            ${renderMessageAttachments(message.attachments || [])}
            <p>${escapeHtml(message.content || "")}</p>
          </div>
        </div>
        <div class="bubble-actions">
          <button class="copy-btn" data-copy="${escapeHtml(message.content || "")}" title="复制">⧉</button>
        </div>
      </article>
    `;
  }

  const linkedRun = run && message.run_id && run.run_id === message.run_id ? run : null;
  const liveStatuses = ["running", "queued", "planned", "responding", "awaiting_approval"];
  const currentRunActive = linkedRun && liveStatuses.includes(linkedRun.status || "");
  const pendingActive = !message.run_id && String(message.id || "").startsWith("pending_agent_") && liveStatuses.includes(message.status || "");
  const chatActive = message.details?.mode === "chat" && liveStatuses.includes(message.status || "");
  const active = Boolean(currentRunActive || pendingActive || chatActive);
  const isError = message.status === "error";
  const thoughts = renderAgentThoughts(message, run, active);
  const details = renderAgentDetails(message, run, llm);
  // 平滑流式：流式中的消息只渲染已释放部分（分批淡入），完成后为全量
  const text = smoothShownContent(message).trim();
  const phase = linkedRun?.phase || message.details?.phase || "";
  const mode = linkedRun?.mode || message.details?.mode || "";
  // 只要还在处理中且尚无任何可见内容（思考块/正文），就显示一个轻量的
  // "思考中"指示行，而不是先渲染一个空气泡等着内容填进来。
  const showThinkingPill = active && !text && !thoughts;
  // 非活动、且完全没有内容的助手消息不渲染，避免留下孤立空气泡。
  if (!active && !text && !thoughts && !details && !isError) return "";
  return `
    <article class="chat-bubble agent${isError ? " error" : ""}" data-message-id="${escapeHtml(message.id || "")}">
      ${isError ? `<div class="error-pill">任务执行失败，详见对话内容</div>` : ""}
      ${showThinkingPill ? `<div class="thinking-pill"><span class="live-dot"></span> ${escapeHtml(humanStatus(message.status, phase, mode))}</div>` : ""}
      ${thoughts}
      ${text ? `<div class="agent-message">${renderMarkdown(text)}</div>` : ""}
      ${details}
      <div class="bubble-actions">
        <button class="copy-btn" data-copy="${escapeHtml(message.content || "")}" title="复制">⧉</button>
      </div>
    </article>
  `;
}

function renderAgentThoughts(message, run, active) {
  const linkedRun = run && message.run_id && run.run_id === message.run_id ? run : null;
  const mode = linkedRun?.mode || message.details?.mode || "";
  const plan = linkedRun?.plan || message.details?.plan;
  const loopState = linkedRun?.loop_state || message.details?.loop_state || {};
  const processTrace = Array.isArray(linkedRun?.process_trace) && linkedRun.process_trace.length
    ? linkedRun.process_trace
    : (Array.isArray(message.details?.process_trace) ? message.details.process_trace : []);
  const decisions = Array.isArray(loopState?.decisions) ? loopState.decisions : [];
  const results = Array.isArray(loopState?.results) ? loopState.results : [];
  const processRows = [];
  if (processTrace.length) {
    processTrace.forEach((item) => {
      const toolName = String(item?.tool || "");
      if (toolName === "memory_store") return;
      const title = item?.tool ? humanToolLabel(item.tool, item.title) : humanThoughtTitle(item?.title || "");
      const body = humanThoughtBody(item?.body || "", item?.tool || "");
      if (title || body) {
        processRows.push({
          status: item?.status || "completed",
          kind: normalizeProcessKind(item),
          title: title || "处理任务",
          body,
        });
      }
    });
  }
  if (!processRows.length) {
    decisions.forEach((decision, index) => {
      const action = String(decision?.action || "");
      if (!action || action === "memory_store") return;
      const result = results.find((item) => Number(item.step_index || 0) === index + 1);
      const status = result ? (result.ok ? "completed" : "failed") : (active ? "running" : "planned");
      processRows.push({
        status,
        kind: "tool",
        title: humanToolLabel(action),
        body: humanDecisionReason(decision.reason || result?.data?.message || "", action),
      });
    });
  }
  const hasToolStep = (plan?.steps || []).some((step) => step?.tool && step.tool !== "memory_store");
  if (!processRows.length && hasToolStep) {
    (plan.steps || []).forEach((step) => {
      if (!step?.tool || step.tool === "memory_store") return;
      processRows.push({
        status: step.status || "planned",
        kind: "tool",
        title: humanToolLabel(step.tool, step.label),
        body: step.result?.message || step.result?.status || "",
      });
    });
  }
  if (!processRows.length) {
    const runTrace = linkedRun?.thought_trace;
    const trace = Array.isArray(runTrace) && runTrace.length ? runTrace : (message.details?.thought_trace || []);
    (Array.isArray(trace) ? trace : []).forEach((item) => {
      const tool = item?.tool || "";
      const title = tool ? humanToolLabel(tool) : humanThoughtTitle(item?.title || "");
      const body = humanThoughtBody(item?.body || item?.title || "", tool);
      if (title || body) {
        processRows.push({
          status: item?.status || "completed",
          kind: normalizeProcessKind(item),
          title: title || "处理任务",
          body,
        });
      }
    });
  }
  if (!processRows.length && message.status !== "error") return "";
  const detailId = `thought_${message.id || message.run_id || ""}`;
  // 过程细节默认始终展开（主流 Agent 风格：思考与工具过程直接可回看），
  // 用户可手动收起；不再在任务完成后自动折叠
  const openAttr = " open";
  const phase = linkedRun?.phase || message.details?.phase || "";
  const summary = active ? "正在处理..." : `已处理${humanRunDuration(linkedRun, message)}`;
  const rows = processRows.map((item) => {
    const status = item.status || "completed";
    const kind = item.kind || "reasoning";
    const badge = processKindLabel(kind);
    const bodyText = String(item.body || "");
    // 长推理文本保留换行（模型思考块可回看完整内容）
    // 长推理文本默认折叠（平铺全文刷屏难受）；短摘要照常平铺
    const isLongReasoning = kind === "reasoning" && (bodyText.length > 200 || bodyText.includes("\n"));
    const body = !bodyText
      ? ""
      : isLongReasoning
        ? `<details class="reasoning-fold"><summary>模型思考 · 点击展开全文</summary><pre class="fold-body">${escapeHtml(bodyText)}</pre></details>`
        : `<p class="${bodyText.length > 160 ? "long-text" : ""}">${escapeHtml(bodyText)}</p>`;
    return `
      <div class="thought-row ${escapeHtml(status)} kind-${escapeHtml(kind)}">
        <span></span>
        <div>
          <strong><em>${escapeHtml(badge)}</em>${escapeHtml(item.title || "思考")}</strong>
          ${body}
        </div>
      </div>
    `;
  }).join("");
  return `
    <details class="thought-block message-detail agent-thoughts" data-detail-id="${escapeHtml(detailId)}"${openAttr}>
      <summary>${active ? `<span class="live-dot"></span>` : ""}${escapeHtml(summary)}</summary>
      <div class="thought-body live-process">${rows}</div>
    </details>
  `;
}

function normalizeProcessKind(item) {
  const explicit = String(item?.kind || "").trim().toLowerCase();
  if (["reasoning", "tool", "skill", "verify", "memory", "system", "plan", "plan_step"].includes(explicit)) {
    return explicit;
  }
  const title = String(item?.title || "").toLowerCase();
  if (item?.tool) return "tool";
  if (/校验|verify|回读/.test(title)) return "verify";
  return "reasoning";
}

function processKindLabel(kind) {
  if (kind === "tool") return "工具";
  if (kind === "skill") return "技能调用";
  if (kind === "plan_step") return "执行计划";
  if (kind === "verify") return "校验";
  if (kind === "memory") return "记忆";
  if (kind === "system") return "系统";
  return "模型思考";
}

function hasProcessTrace(message, run) {
  const linkedRun = run && message.run_id && run.run_id === message.run_id ? run : null;
  return Boolean(
    (Array.isArray(linkedRun?.process_trace) && linkedRun.process_trace.length) ||
    (Array.isArray(message.details?.process_trace) && message.details.process_trace.length)
  );
}

function humanRunDuration(run, message) {
  const started = Number(run?.started_at || message?.details?.started_at || message?.created_at || 0);
  const finished = Number(run?.finished_at || message?.details?.finished_at || message?.updated_at || 0);
  if (!started || !finished || finished < started) return "";
  const seconds = Math.max(0, Math.round(finished - started));
  if (seconds < 1) return " <1s";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (!minutes) return ` ${seconds}s`;
  return ` ${minutes}m ${rest}s`;
}

function humanThoughtTitle(title) {
  const text = String(title || "").trim();
  if (/循环决策/i.test(text)) return "选择下一步动作";
  if (/理解|understand/i.test(text)) return "理解任务";
  if (/工具|tool/i.test(text)) return "选择工具";
  if (/校验|verify/i.test(text)) return "校验结果";
  return text;
}

function humanThoughtBody(body, tool = "") {
  const text = String(body || "").trim();
  if (tool) return humanDecisionReason(text, tool);
  return humanDecisionReason(text);
}

function renderAgentDetails(message, run, llm) {
  return "";
}

function renderAgentState(agentState) {
  if (!agentState || typeof agentState !== "object") return "";
  const vehicle = agentState.vehicle || {};
  const pos = vehicle.position_ned || {};
  const rows = [
    ["后端", agentState.backend_name || agentState.backend || "-"],
    ["连接", agentState.connected ? "已连接" : (agentState.stale_connection ? "异常/过期" : "未连接")],
    ["Ready", String(agentState.ready === true)],
    ["Armed", valueOrDash(vehicle.armed)],
    ["Flying", valueOrDash(vehicle.flying)],
    ["位置", typeof pos === "object" && Object.keys(pos).length ? `x=${valueOrDash(pos.x)} y=${valueOrDash(pos.y)} z=${valueOrDash(pos.z)}` : "-"],
    ["碰撞", valueOrDash(vehicle.has_collided ?? vehicle.collision)],
  ];
  const activeRun = agentState.active_run;
  if (activeRun) {
    rows.push(["当前任务", `${activeRun.phase || activeRun.status || "-"} · ${valueOrDash(activeRun.progress)}%`]);
  }
  const activeOperation = agentState.active_operation;
  if (activeOperation) {
    rows.push([
      "后台操作",
      `${activeOperation.status || "running"}${activeOperation.task_id ? ` · ${activeOperation.task_id}` : ""}`,
    ]);
  }
  return `
    <div class="detail-note agent-state-note">
      <strong>Agent 状态感知</strong>
      <div class="agent-state-grid">
        ${rows.map(([label, value]) => `
          <span>${escapeHtml(label)}</span>
          <code>${escapeHtml(String(value))}</code>
        `).join("")}
      </div>
    </div>
  `;
}

function valueOrDash(value) {
  return value === undefined || value === null || value === "" ? "-" : value;
}

function humanDecisionReason(reason, action = "") {
  const text = String(reason || "").trim();
  const normalized = text.toLowerCase();
  if (action === "airsim_take_photo" || normalized.includes("capture the current camera frame")) {
    return "获取当前摄像头画面";
  }
  if (action === "inspect_current_frame" || normalized.includes("analyze the current camera frame") || normalized.includes("confirm the requested target")) {
    return normalized.includes("confirm the requested target") ? "确认画面中是否存在目标" : "调用所选多模态模型分析画面";
  }
  if (normalized.includes("visual analysis/confirmation has completed")) {
    return "视觉分析已完成，准备输出结果";
  }
  if (normalized.includes("target is visible") && normalized.includes("2d image target")) {
    return "目标只在二维画面中确认，缺少安全飞行所需的三维位置";
  }
  return text;
}

function renderSkillSubTools(result) {
  const toolResults = result?.data?.tool_results || result?.data?.accepted_result?.tool_results;
  if (!Array.isArray(toolResults) || !toolResults.length) return "";
  return `
    <div class="sub-tool-list">
      ${toolResults.map((item) => {
        const ok = item.ok === true;
        const tool = item.tool || item.name || "tool";
        const message = item.data?.message || item.data?.status || "";
        return `<span class="${ok ? "ok" : "fail"}">${escapeHtml(tool)}${message ? ` · ${escapeHtml(message)}` : ""}</span>`;
      }).join("")}
    </div>
  `;
}



function renderPendingCommand(command, mode = "chat", attachments = []) {
  const serial = ++pendingMessageCounter;
  const isExecute = mode === "execute";
  const createdAt = Date.now() / 1000;
  const userId = `pending_user_${Date.now()}_${serial}`;
  const agentId = `pending_agent_${Date.now()}_${serial}`;
  localPendingMessages.push(
    {
      id: userId,
      role: "user",
      content: command,
      attachments,
      status: "complete",
      pending: true,
      created_at: createdAt,
      pending_created_at: createdAt,
    },
    {
      id: agentId,
      role: "assistant",
      content: isExecute ? "正在准备任务..." : "正在生成回复...",
      status: "running",
      pending: true,
      created_at: createdAt,
      pending_created_at: createdAt,
      details: {
        mode,
        phase: isExecute ? "understanding" : "responding",
        thought_trace: [{
          title: isExecute ? "理解指令" : "读取上下文",
          body: isExecute ? "正在准备进入规划与执行流程。" : "Chat 模式正在基于上下文生成回复。",
          status: "running",
        }],
      },
    },
  );
  pendingScrollTargetId = userId;
  forceNextChatScroll = true;
  renderChat(latestState?.messages || [], latestState?.current_run || null, latestState?.llm || {});
  return { userId, agentId };
}

function bindPendingRunId(agentId, runId) {
  const pending = localPendingMessages.find((message) => message.id === agentId);
  if (!pending) return;
  pending.run_id = runId;
  renderChat(latestState?.messages || [], latestState?.current_run || null, latestState?.llm || {});
}

function clearPendingCommand(pendingCommand = {}) {
  const ids = new Set([pendingCommand.userId, pendingCommand.agentId].filter(Boolean));
  if (!ids.size) return;
  localPendingMessages = localPendingMessages.filter((message) => !ids.has(message.id));
  renderChat(latestState?.messages || [], latestState?.current_run || null, latestState?.llm || {});
}

// 提交成功后的兜底状态同步：正常路径完全由 SSE 推进（message_create/
// message_update/message_delta/run_update），不做整页重渲染。只有事件流
// 已断开、且稍后仍收不到该 run 的消息时才拉一次 /api/state。
function schedulePostSubmitFallback(runId = "") {
  window.setTimeout(() => {
    const messages = Array.isArray(latestState?.messages) ? latestState.messages : [];
    if (runId && messages.some((message) => message.run_id === runId)) return; // SSE 已送达
    if (streamSource && streamSource.readyState !== 2) return; // 流在线/重连中，继续等推送
    refresh().catch(() => {});
  }, 900);
}

function humanStatus(status, phase = "", mode = "") {
  if (mode === "chat" && status === "running") return "Chat 回复中...";
  if (phase === "understanding") return "正在理解...";
  if (phase === "planning") return "正在规划...";
  if (phase === "executing") return "正在执行...";
  if (phase === "verifying") return "正在校验...";
  if (phase === "responding") return "正在整理结果...";
  if (phase === "awaiting_approval") return "等待确认...";
  if (status === "planned") return "正在规划...";
  if (status === "queued") return "等待执行...";
  if (status === "running") return "正在处理...";
  if (status === "responding") return "正在整理结果...";
  return "处理中...";
}

function scrollChatToEnd() {
  // 立即贴底一次，再在下一帧补一次：后台标签/被节流的渲染环境里
  // requestAnimationFrame 可能长时间不执行，只靠 rAF 会表现为"不跟随"。
  pinChatToBottom();
  requestAnimationFrame(() => {
    pinChatToBottom();
  });
}

function scrollMessageIntoView(messageId) {
  requestAnimationFrame(() => {
    const target = [...els.chatThread.querySelectorAll("[data-message-id]")]
      .find((item) => item.getAttribute("data-message-id") === messageId);
    if (!target) {
      pendingScrollTargetId = "";
      scrollChatToEnd();
      return;
    }
    target.scrollIntoView({ block: "center", inline: "nearest" });
    pendingScrollTargetId = "";
  });
}

function shouldStickToChatBottom() {
  if (!els.chatThread) return true;
  const distance = els.chatThread.scrollHeight - els.chatThread.scrollTop - els.chatThread.clientHeight;
  return distance < 96;
}

function isFenceLine(line) {
  return /^\s*(```|~~~)/.test(line);
}

function isTableLine(line) {
  return /^\s*\|.*\|\s*$/.test(line);
}

function extendToSafeMarkdown(text, pos) {
  if (pos >= text.length) return pos;
  let fenceFrom = -1;
  let inFence = false;
  let inTable = false;
  let tableFrom = -1;
  const lines = text.split("\n");
  let offset = 0;
  for (const line of lines) {
    const start = offset;
    offset += line.length + 1;
    if (isFenceLine(line)) {
      inFence = !inFence;
      if (inFence) fenceFrom = start;
      else fenceFrom = -1;
      inTable = false;
      tableFrom = -1;
      if (start >= pos) break;
      continue;
    }
    if (!inFence && isTableLine(line)) {
      if (!inTable) {
        inTable = true;
        tableFrom = start;
      }
    } else if (inTable && line.trim() === "") {
      inTable = false;
      tableFrom = -1;
    } else if (inTable && !isTableLine(line)) {
      inTable = false;
      tableFrom = -1;
    }
    if (start >= pos) break;
  }
  // 未闭合的代码块/表格：退回到它们的起点再释放。注意起点为 0 时同样成立
  // （此前 `> 0` 会把"整段以未闭合 fence 开头"误判成安全切点）。
  if (inFence) return fenceFrom >= 0 ? fenceFrom : pos;
  if (inTable) return tableFrom >= 0 ? tableFrom : pos;
  return pos;
}

function smoothParagraphTarget(text, shown, minChars = 24) {
  const need = shown + minChars;
  if (text.length < need) return shown;
  let pos = -1;
  const para = text.indexOf("\n\n", need);
  if (para !== -1) pos = para + 2;
  else {
    const nl = text.indexOf("\n", need);
    if (nl !== -1) pos = nl + 1;
    // 单段长文本（整段没有换行）没有可用的段落/行边界：按最小批量释放。
    // 否则整段正文要等消息完成（smoothFlushMessage）才一次性出现，
    // 生成期间气泡只有状态占位，操作员会觉得"文字迟迟不出来"。
    else pos = Math.min(text.length, need);
  }
  if (pos === -1) return shown;
  pos = extendToSafeMarkdown(text, pos);
  return pos > shown ? pos : shown;
}

function smoothQueueDelta(message) {
  const id = message.id || message.run_id;
  if (!id) return;
  smoothStream.targets.set(id, String(message.content || ""));
  if (!smoothStream.shown.has(id)) smoothStream.shown.set(id, 0);
  smoothStartLoop();
}

// 渲染层取该消息当前应显示的内容（流式中的消息显示已释放部分）
function smoothShownContent(message) {
  const id = message.id || message.run_id;
  if (id && smoothStream.targets.has(id)) {
    const target = smoothStream.targets.get(id) || "";
    const shown = smoothStream.shown.get(id) || 0;
    return target.slice(0, shown);
  }
  return String(message.content || "");
}

function smoothStartLoop() {
  if (smoothStream.timer) return;
  smoothStream.timer = window.setInterval(() => {
    let active = false;
    for (const [id, target] of smoothStream.targets) {
      const shown = smoothStream.shown.get(id) || 0;
      if (shown >= target.length) continue;
      active = true;
      const pos = smoothParagraphTarget(target, shown);
      if (pos > shown) {
        smoothStream.shown.set(id, pos);
        scheduleChatRender();
        if (chatAutoFollow) pinChatToBottom();
      }
    }
    if (!active) {
      window.clearInterval(smoothStream.timer);
      smoothStream.timer = null;
    }
  }, 160);
}

function connectEventStream() {
  if (!window.EventSource || streamSource) return;
  streamSource = new EventSource("/api/stream");

  ["snapshot", "message_create", "message_update", "message_delta", "run_update", "runtime_event", "task_runs_update"].forEach((name) => {
    streamSource.addEventListener(name, (event) => handleStreamEvent(name, parseStreamData(event)));
  });

  streamSource.onerror = () => {
    if (streamSource) {
      streamSource.close();
      streamSource = null;
    }
    window.clearTimeout(streamReconnectTimer);
    streamReconnectTimer = window.setTimeout(connectEventStream, 1200);
  };
}

function parseStreamData(event) {
  try {
    return JSON.parse(event.data || "{}");
  } catch (_) {
    return {};
  }
}

function handleStreamEvent(type, payload) {
  if (!payload) return;
  if (type === "snapshot") {
    latestState = applyCachedSessionHistory(payload);
    render(latestState);
    loadCurrentSessionHistory().catch(() => {});
    syncRosTelemetryStream();
    return;
  }

  if (!latestState) return;

  if (type === "message_create" || type === "message_update") {
    upsertMessage(payload);
    if (payload.message && ["complete", "error", "cancelled"].includes(payload.message.status)) {
      smoothFlushMessage(payload.message.id);
    }
    scheduleChatRender();
    return;
  }

  if (type === "message_delta") {
    if (payload.message) {
      upsertMessage(payload.message);
      smoothQueueDelta(payload.message);
    } else {
      updateMessageContent(payload.id, payload.content);
    }
    scheduleChatRender();
    return;
  }

  if (type === "run_update") {
    latestState.current_run = payload;
    latestState.runtime = latestState.runtime || {};
    latestState.runtime.status = payload.status || latestState.runtime.status;
    // run 进度事件很密集（提交后立刻就有一次）。这里只更新与 run 直接相关的
    // 区域，不再走 render(latestState) 的整页重建（工具清单/事件流/会话列表/
    // Skill 列表都是整段 innerHTML），那是提交瞬间可感卡顿的主要来源。
    renderTopbar(payload, latestState.tool_runtime || {}, latestState.supervisor || {}, latestState.llm || {});
    renderPlan(payload);
    renderApprovalDialog(payload, latestState.pending_approvals || []);
    updateMapView(latestState);
    scheduleChatRender();
    syncCommandSubmitState();
    syncRosTelemetryStream();
    return;
  }

  if (type === "runtime_event") {
    latestState.events = latestState.events || [];
    latestState.events.push(payload);
    latestState.events = latestState.events.slice(-80);
    renderEvents(latestState.events);
    return;
  }

  if (type === "task_runs_update") {
    latestState.task_runs = payload;
    latestState.memory = latestState.memory || {};
    latestState.memory.task_runs = payload;
    renderTaskRuns(payload);
    renderMemory(latestState.memory);
  }
}

function upsertMessage(message) {
  if (!message || !message.id) return;
  removePendingForServerMessage(message);
  latestState.messages = latestState.messages || [];
  const index = latestState.messages.findIndex((item) => item.id === message.id);
  if (index >= 0) latestState.messages[index] = message;
  else latestState.messages.push(message);
  const sessionId = latestState?.current_session?.id || "";
  if (sessionId && fullSessionMessageCache.has(sessionId)) {
    fullSessionMessageCache.set(sessionId, mergeSessionMessages(fullSessionMessageCache.get(sessionId), [message]));
  }
}

function updateMessageContent(id, content) {
  if (!id || content == null || !latestState?.messages) return;
  const message = latestState.messages.find((item) => item.id === id);
  if (message) message.content = content;
}

function renderTools(tools, toolCards = [], toolRuntime = {}) {
  normalizeAgentSettingsCopy();
  const rawTools = Array.isArray(tools) ? tools : [];
  const rawByName = new Map(rawTools.map((tool) => [tool.name, tool]));
  const cards = Array.isArray(toolCards) ? toolCards : [];
  const visibleTools = cards.length
    ? cards.map((card) => ({ ...card, manifest: card.manifest || rawByName.get(card.name)?.manifest || {} }))
    : rawTools.filter((tool) => !isWorkflowMigrationRecord(tool));
  const migrationTools = rawTools.filter(isWorkflowMigrationRecord);
  const internalTools = rawTools.filter((tool) => (tool.manifest || {}).kind === "internal");

  els.toolCount.textContent = `${visibleTools.length} atomic/provider`;
  if (!visibleTools.length && !migrationTools.length) {
    els.toolList.innerHTML = `<div class="empty">Tools are not loaded yet.</div>`;
    return;
  }

  const architecture = renderBackendModeNote(toolRuntime);
  const grouped = new Map();
  visibleTools.forEach((tool) => {
    const category = toolCategory(tool);
    if (!grouped.has(category)) grouped.set(category, []);
    grouped.get(category).push(tool);
  });
  const categoryOrder = ["链路", "遥测", "飞控", "导航", "任务", "感知", "安全", "系统", "其他"];
  const categorySections = [...grouped.entries()]
    .sort(([left], [right]) => categoryOrder.indexOf(left) - categoryOrder.indexOf(right))
    .map(([category, items]) => renderToolSection(category, items, "atomic"))
    .join("");
  els.toolList.innerHTML = [
    architecture,
    `<label class="tool-search"><span>搜索工具</span><input type="search" placeholder="名称、能力或说明"></label>`,
    `<div class="tool-category-groups">${categorySections}</div>`,
    migrationTools.length ? `<details class="settings-advanced"><summary>迁移记录 (${migrationTools.length})</summary>${renderToolSection("仅供兼容，不参与规划", migrationTools, "migration")}</details>` : "",
    internalTools.length ? `<details class="settings-advanced"><summary>运行时内部工具 (${internalTools.length})</summary>${renderToolSection("内部工具", internalTools, "internal")}</details>` : "",
  ].filter(Boolean).join("");
  const search = els.toolList.querySelector(".tool-search input");
  if (search) {
    search.addEventListener("input", () => {
      const query = search.value.trim().toLowerCase();
      els.toolList.querySelectorAll(".tool-card-item").forEach((card) => {
        card.hidden = Boolean(query) && !card.textContent.toLowerCase().includes(query);
      });
      els.toolList.querySelectorAll(".settings-list-section").forEach((section) => {
        const cardsInSection = [...section.querySelectorAll(".tool-card-item")];
        section.hidden = cardsInSection.length > 0 && cardsInSection.every((card) => card.hidden);
      });
    });
  }
}

function toolCategory(tool) {
  const key = tool?.name || "";
  const manifest = tool?.manifest || {};
  const localized = TOOL_LOCALE[key]?.category;
  if (localized) return localized;
  const raw = String(tool?.category || manifest.category || manifest.surface || "").toLowerCase();
  if (TOOL_CATEGORY_LOCALE[raw]) return TOOL_CATEGORY_LOCALE[raw];
  if (raw.includes("telemetry") || raw.includes("status")) return "遥测";
  if (raw.includes("mission")) return "任务";
  if (raw.includes("navigation") || raw.includes("position")) return "导航";
  if (raw.includes("camera") || raw.includes("image") || raw.includes("perception")) return "感知";
  if (raw.includes("safety")) return "安全";
  return "其他";
}

function renderToolSection(title, tools, mode) {
  if (!tools.length) return "";
  return `
    <section class="settings-list-section">
      <div class="settings-section-head"><strong>${escapeHtml(title)}</strong><span>${tools.length}</span></div>
      ${tools.map((tool) => renderToolCardItem(tool, mode)).join("")}
    </section>
  `;
}

function renderToolCardItem(tool, mode = "atomic") {
  const key = tool.name || "";
  const manifest = tool.manifest || {};
  const localized = TOOL_LOCALE[key] || {};
  const replacement = manifest.replacement_skill || "";
  const desc = mode === "migration"
    ? (manifest.notes || `Legacy workflow record. Use ${replacement || "the matching skill"} instead.`)
    : (localized.desc || tool.purpose || tool.description || manifest.notes || "");
  const badges = [];
  badges.push(mode === "migration" ? "skill migration" : (manifest.kind || tool.kind || "atomic"));
  if (manifest.surface) badges.push(manifest.surface);
  if (tool.execution_mode === "async") badges.push("async start only");
  if (replacement) badges.push(`replace: ${replacement}`);
  if (tool.risk) badges.push(`risk ${tool.risk}`);
  return `
    <article class="compact-item tool-card-item" title="${escapeHtml(key)}">
      <strong>${escapeHtml(localized.name || key)}</strong>
      ${localized.name ? `<code>${escapeHtml(key)}</code>` : ""}
      <div class="tool-meta">${badges.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>
      <p>${escapeHtml(desc || "No description.")}</p>
    </article>
  `;
}

function isWorkflowMigrationRecord(tool) {
  const manifest = tool?.manifest || {};
  return manifest.kind === "workflow" && manifest.recommended_layer === "skill";
}

function renderMemory(memory) {
  normalizeAgentSettingsCopy();
  const lessons = memory.lessons || [];
  const risks = memory.risk_events || [];
  const missions = memory.missions || [];
  const candidates = memory.skill_candidates || [];
  const taskRuns = Array.isArray(memory.task_runs?.recent) ? memory.task_runs.recent : [];
  const conversation = memory.conversation || {};
  const working = memory.working_state || memory.session || {};
  const persistentCount = lessons.length + risks.length + missions.length + candidates.length;
  els.memoryCount.textContent = `${persistentCount} 条长期记忆 / ${conversation.messages_saved || 0} 条会话消息`;

  const modelContext = [
    {
      title: "当前会话",
      text: `完整保存 ${conversation.messages_saved || 0} 条消息；本次模型上下文装载 ${conversation.messages_sent_to_model || 0} 条，估算 ${Number(conversation.estimated_context_tokens || 0).toLocaleString()} / ${Number(conversation.context_window || 0).toLocaleString()} tokens (${Number(conversation.context_percent || 0).toFixed(1)}%)。`,
      tag: "上下文",
    },
    {
      title: "当前运行态",
      text: Object.keys(working).length ? compactJson(working, 220) : "尚未保存位置、任务起点或短期运行状态。",
      tag: "运行态",
    },
  ];
  const persistent = [
    ...missions.slice(0, 6).map((item) => ({
      title: item.summary || item.intent || "任务经验",
      text: `${item.status || "unknown"} · ${toolSequenceText(item.tool_sequence) || item.command || "无工具序列"}`,
      tag: "任务",
    })),
    ...lessons.slice(0, 5).map((item) => ({
      title: item.intent || "成功经验",
      text: item.summary || `success rate ${item.success_rate ?? "-"}`,
      tag: "经验",
    })),
    ...risks.slice(0, 5).map((item) => ({
      title: item.run_id || "风险记录",
      text: item.reason || item.command || "无风险详情。",
      tag: "风险",
    })),
  ];
  const skillCandidates = candidates.slice(0, 8).map((item) => ({
    title: item.intent || "Skill 候选",
    text: `${toolSequenceText(item.tool_sequence) || "无工具序列"} · ${item.successes || 0}/${item.runs || 0} 次成功${item.eligible_for_review ? " · 可评审" : " · 继续积累样本"}`,
    tag: "候选",
  }));
  const replays = taskRuns.slice(0, 6).map((item) => ({
    title: item.summary || item.command || "任务复盘",
    text: `${readableRunStatus(item.status)} · ${item.counters?.steps_ok || 0}/${item.counters?.steps_total || 0} 步完成 · ${item.counters?.events || 0} 个事件`,
    tag: "复盘",
  }));

  els.memoryList.innerHTML = [
    renderMemoryGroup("模型当前可用", modelContext),
    renderMemoryGroup("长期任务经验", persistent),
    renderMemoryGroup("待沉淀为 Skill", skillCandidates),
    renderMemoryGroup("任务复盘", replays),
  ].join("");
}

function renderMemoryGroup(title, items) {
  const rows = items.length
    ? items.map((item) => `
      <article class="compact-item memory-row">
        <div class="memory-item-head"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.tag || "MEMORY")}</small></div>
        <p>${escapeHtml(item.text || "")}</p>
      </article>
    `).join("")
    : `<div class="empty small">暂无记录</div>`;
  return `
    <section class="memory-group">
      <div class="settings-section-head"><strong>${escapeHtml(title)}</strong><span>${items.length}</span></div>
      ${rows}
    </section>
  `;
}

function toolSequenceText(sequence) {
  return Array.isArray(sequence) ? sequence.filter(Boolean).join(" -> ") : "";
}

function readableRunStatus(status) {
  const value = String(status || "").toLowerCase();
  return {
    completed: "completed",
    failed: "failed",
    blocked: "blocked",
    planned: "planned",
    running: "running",
    cancelled: "cancelled",
    canceled: "cancelled",
  }[value] || value || "unknown";
}

// 「实际心跳来源」= 后端真收到 MAVLink 数据报的地址。监听模式（udpin:0.0.0.0）
// 下 socket 绑全部网卡，所以它才是"当前到底连着哪条链路"的判据；配置里的
// PX4 目标只是用户填的期望值，两者可能不是同一台设备。
function actualHeartbeatSourceText(link = currentActualLink()) {
  const peer = String(link.actual_peer_endpoint || "");
  if (!peer) return "尚未收到心跳";
  return link.peer_source_verified === false ? `${peer}（按发送目标推断，未核实）` : peer;
}

function valueText(value, fallback = "--") {
  return value === undefined || value === null || value === "" ? fallback : String(value);
}

function setupConnected() {
  const runtime = latestState?.tool_runtime || {};
  const setup = setupSnapshot();
  return Boolean(setup.connected || (runtime.connected && !runtime.stale_connection));
}

function setupStatusClass(status) {
  return {
    ok: "ready",
    ready: "ready",
    warning: "warning",
    needs_attention: "warning",
    partial: "warning",
    disabled: "muted",
    missing: "danger",
    error: "danger",
    disconnected: "muted",
  }[String(status || "").toLowerCase()] || "muted";
}

function setupStatusLabel(status) {
  return {
    ok: "就绪",
    ready: "就绪",
    warning: "需要设置",
    needs_attention: "需要检查",
    partial: "部分收到",
    disabled: "禁用",
    missing: "未检测",
    error: "错误",
    disconnected: "未连接",
  }[String(status || "").toLowerCase()] || valueText(status);
}

function setupBadge(status, label = setupStatusLabel(status)) {
  return `<span class="setup-badge ${setupStatusClass(status)}">${escapeHtml(label)}</span>`;
}

function summaryCard(section, title, summary = {}, rows = []) {
  const status = summary.setup || (setupConnected() ? "warning" : "disconnected");
  return `
    <article class="setup-summary-card ${setupStatusClass(status)}" data-system-section="${escapeHtml(section)}" role="button" tabindex="0">
      <button type="button" class="setup-card-head" data-system-section="${escapeHtml(section)}">
        <span>${escapeHtml(title)}</span>
        <span class="setup-status-dot ${setupStatusClass(status)}"></span>
      </button>
      ${setupRows(rows)}
    </article>
  `;
}

function timestampText(value) {
  const seconds = Number(value || 0);
  if (!Number.isFinite(seconds) || seconds <= 0) return "--";
  return new Date(seconds * 1000).toLocaleTimeString();
}

function normalizeRosGatewayUrl(value) {
  const text = String(value || "").trim();
  if (!text) return "http://127.0.0.1:8766";
  if (/^https?:\/\//i.test(text)) return text;
  return `http://${text}`;
}

