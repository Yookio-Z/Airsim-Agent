/* // 命令表单与模式切换：submit/keydown、图片粘贴、发送态管理、系统设置模态绑定 */

function setCommandMode(mode) {
  commandMode = mode === "execute" ? "execute" : "chat";
  localStorage.setItem("airsim-agent-command-mode", commandMode);
  if (els.chatModeBtn) els.chatModeBtn.classList.toggle("active", commandMode === "chat");
  if (els.executeModeBtn) els.executeModeBtn.classList.toggle("active", commandMode === "execute");
  if (els.commandInput) {
    els.commandInput.placeholder = commandMode === "execute"
      ? "Execute a flight command..."
      : "Chat, ask status, or clarify a plan...";
  }
  const submitButton = els.commandForm?.querySelector("button[type='submit']");
  if (submitButton) {
    submitButton.classList.toggle("execute", commandMode === "execute");
    submitButton.title = commandMode === "execute" ? "执行任务 (Enter)" : "发送聊天 (Enter)";
  }
  syncCommandSubmitState();
}

if (els.chatModeBtn) els.chatModeBtn.addEventListener("click", () => setCommandMode("chat"));
if (els.executeModeBtn) els.executeModeBtn.addEventListener("click", () => setCommandMode("execute"));
setCommandMode(commandMode);

const commandSubmitButton = els.commandForm?.querySelector("button[type='submit']");
if (commandSubmitButton) {
  commandSubmitButton.addEventListener("click", async (event) => {
    if (!isAgentWorkActive()) return;
    event.preventDefault();
    try {
      await cancelActiveWork();
    } finally {
      els.commandForm.requestSubmit();
    }
  });
}

els.commandInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.commandForm.requestSubmit();
  }
});

if (els.attachImageBtn && els.imageInput) {
  els.attachImageBtn.addEventListener("click", () => els.imageInput.click());
  els.imageInput.addEventListener("change", async () => {
    await addImageFiles([...els.imageInput.files]);
    els.imageInput.value = "";
  });
}

els.commandInput.addEventListener("paste", async (event) => {
  const files = [...(event.clipboardData?.items || [])]
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter(Boolean);
  if (!files.length) return;
  event.preventDefault();
  await addImageFiles(files);
});

if (els.imagePreview) {
  els.imagePreview.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-image]");
    if (!button) return;
    pendingImages.splice(Number(button.dataset.removeImage), 1);
    renderImagePreview();
  });
}

els.commandForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  // 打断语义：任务执行中也允许发送，服务端会自动中断旧任务后执行新指令
  // （“打断对话即后台停止调用”）。输入框始终在提交后清空。
  const command = els.commandInput.value.trim();
  if (!command && !pendingImages.length) return;
  let selectedModel = modelsCache.find((model) => model.id === els.modelSelector.value);
  if (pendingImages.length && !selectedModel?.multimodal && applicationSettings.agent.auto_select_multimodal_model) {
    const visionModel = modelsCache.find((model) => model.multimodal && model.enabled);
    if (visionModel) {
      setSelectedModelId(visionModel.id);
      selectedModel = visionModel;
      showNotice(`已自动切换到图像模型 ${visionModel.name}`, "info");
    }
  }
  if (pendingImages.length && !selectedModel?.multimodal) {
    showNotice("未检测到可用图像模型，请在模型设置中修正模型 ID 或手动指定输入能力", "error");
    return;
  }
  const effectiveCommand = command || "请分析我提供的图片。";
  const attachments = pendingImages.map((item) => ({
    name: item.name,
    mime_type: item.mime_type,
    data_url: item.data_url,
  }));

  els.commandInput.value = "";
  const submitButton = els.commandForm.querySelector("button[type='submit']");
  submitButton.disabled = true;
  const mode = commandMode === "execute" ? "execute" : "chat";
  const pendingCommand = renderPendingCommand(effectiveCommand, mode, pendingImages);
  try {
    showNotice(mode === "execute" ? "任务已提交，正在执行..." : "正在回复...", "info");
    const resp = await post("/api/command", {
      command: effectiveCommand,
      mode,
      execute: mode === "execute",
      model: els.modelSelector.value,
      attachments,
    });
    if (resp?.run_id) bindPendingRunId(pendingCommand.agentId, resp.run_id);
    pendingImages = [];
    renderImagePreview();
    await refresh();
    if (resp && resp.ok) {
      showNotice(mode === "execute" ? "任务已进入执行流程" : "Chat 已提交，正在生成回复", "success");
    } else {
      // 服务端拒绝了提交：清掉“正在理解指令”的 pending 气泡，避免它
      // 与错误消息并存变红，让用户误以为任务失败后还会继续执行
      clearPendingCommand(pendingCommand);
      showNotice((resp && resp.result && resp.result.data && resp.result.data.message) || "指令处理失败", "error");
    }
  } catch (error) {
    clearPendingCommand(pendingCommand);
    // 输入框保持已清空状态：用户已按“发送”，指令不应再被放回框里
    await refresh().catch(() => {});
    showNotice(error.message || "任务提交失败", "error");
  } finally {
    submitButton.disabled = false;
    syncCommandSubmitState();
  }
});

function getCommandSubmitButton() {
  return els.commandForm?.querySelector("button[type='submit']") || null;
}

function isLiveRunStatus(status) {
  return ["queued", "running", "paused", "responding", "awaiting_approval"].includes(String(status || ""));
}

function isAgentWorkActive() {
  const run = latestState?.current_run;
  if (run && isLiveRunStatus(run.status)) return true;
  const messages = Array.isArray(latestState?.messages) ? latestState.messages : [];
  return messages.some((message) => (
    message?.role === "assistant"
    && message?.status === "running"
    && ["chat", "execute", "plan"].includes(String(message?.details?.mode || ""))
  ));
}

function syncCommandSubmitState() {
  const submitButton = getCommandSubmitButton();
  if (!submitButton) return;
  const active = isAgentWorkActive();
  submitButton.classList.toggle("execute", commandMode === "execute" && !active);
  submitButton.classList.toggle("busy", active);
  submitButton.disabled = false;
  submitButton.textContent = active ? "" : "↑";
  submitButton.title = active
    ? "任务执行中，发送将中断当前任务"
    : (commandMode === "execute" ? "执行任务 (Enter)" : "发送聊天 (Enter)");
  submitButton.setAttribute("aria-label", active ? "中断并发送新指令" : "发送");
}

async function cancelActiveWork() {
  const submitButton = getCommandSubmitButton();
  if (submitButton) submitButton.disabled = true;
  try {
    const result = await invokeFlightControl("cancel");
    await refresh().catch(() => {});
    showNotice(result?.ok ? "已发送中断请求" : (result?.error || "中断请求失败"), result?.ok ? "info" : "error");
  } catch (error) {
    showNotice(error.message || "中断请求失败", "error");
  } finally {
    if (submitButton) submitButton.disabled = false;
    syncCommandSubmitState();
  }
}

async function addImageFiles(files) {
  const allowed = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
  for (const file of files) {
    if (pendingImages.length >= 4) {
      showNotice("每条消息最多附加 4 张图片", "error");
      break;
    }
    if (!allowed.has(file.type)) {
      showNotice(`不支持的图片格式: ${file.type || file.name}`, "error");
      continue;
    }
    if (file.size > 5 * 1024 * 1024) {
      showNotice(`${file.name} 超过 5 MB`, "error");
      continue;
    }
    const total = pendingImages.reduce((sum, item) => sum + item.size, 0) + file.size;
    if (total > 12 * 1024 * 1024) {
      showNotice("单条消息图片总大小不能超过 12 MB", "error");
      break;
    }
    pendingImages.push({
      name: file.name || `clipboard-${Date.now()}.png`,
      mime_type: file.type,
      size: file.size,
      data_url: await fileToDataUrl(file),
    });
  }
  renderImagePreview();
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("图片读取失败"));
    reader.readAsDataURL(file);
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

document.addEventListener("click", async (event) => {
  const copyBtn = event.target.closest(".copy-btn");
  if (copyBtn) {
    const text = copyBtn.dataset.copy || "";
    if (text && navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      copyBtn.textContent = "✓";
      setTimeout(() => { copyBtn.textContent = "⧉"; }, 1200);
    }
    return;
  }

  // P5: approval dialog buttons
  const approveBtn = event.target.closest("[data-approve-run]");
  if (approveBtn) {
    event.stopPropagation();
    approveBtn.disabled = true;
    await approveRun(approveBtn.dataset.approveRun);
    return;
  }
  const rejectBtn = event.target.closest("[data-reject-run]");
  if (rejectBtn) {
    event.stopPropagation();
    rejectBtn.disabled = true;
    await rejectRun(rejectBtn.dataset.rejectRun);
    return;
  }

  const settingsTab = event.target.closest("[data-settings-tab]");
  if (settingsTab) {
    const drawer = settingsTab.closest(".settings-drawer") || document;
    setSettingsTab(settingsTab.dataset.settingsTab, drawer);
    return;
  }

  const systemSection = event.target.closest("[data-system-section]");
  if (systemSection) {
    setSystemSettingsSection(systemSection.dataset.systemSection);
    return;
  }

  const pidView = event.target.closest("[data-pid-view]");
  if (pidView) {
    activePidTuningView = pidView.dataset.pidView || "rate_roll";
    renderVehiclePidPanel(false);
    return;
  }

  const sensorTab = event.target.closest("[data-sensor-setup-tab]");
  if (sensorTab) {
    activeSensorSetupTab = sensorTab.dataset.sensorSetupTab || "imu";
    renderVehicleSensorsPanel(true);
    return;
  }

  const waveWindow = event.target.closest("[data-wave-window]");
  if (waveWindow) {
    activeWaveformWindowSec = Number(waveWindow.dataset.waveWindow || 10) || 10;
    renderVehicleWaveformPanel(false);
    return;
  }

  const waveRate = event.target.closest("[data-wave-rate]");
  if (waveRate) {
    activeWaveformSampleHz = Number(waveRate.dataset.waveRate || 20) || 20;
    renderVehicleWaveformPanel(false);
    return;
  }

  const wavePause = event.target.closest("[data-wave-pause]");
  if (wavePause) {
    vehicleWaveformPaused = !vehicleWaveformPaused;
    vehicleWaveformFrozenHistory = vehicleWaveformPaused ? structuredClone(setupSnapshot().history || {}) : null;
    renderVehicleWaveformPanel(false);
    return;
  }

  const waveClear = event.target.closest("[data-wave-clear]");
  if (waveClear) {
    vehicleWaveformFrozenHistory = {};
    renderVehicleWaveformPanel(false);
    return;
  }

  const waveToggle = event.target.closest("[data-wave-key]");
  if (waveToggle) {
    const key = waveToggle.dataset.waveKey || "";
    if (key) {
      if (waveToggle.checked) selectedVehicleWaveformKeys.add(key);
      else selectedVehicleWaveformKeys.delete(key);
    }
    renderVehicleWaveformPanel(false);
    return;
  }

  const paramGroup = event.target.closest("[data-param-group]");
  if (paramGroup && els.vehicleParameterSearch) {
    els.vehicleParameterSearch.value = paramGroup.dataset.paramGroup || "";
    loadVehicleParameters(false).catch(() => {});
    return;
  }

  const paramSave = event.target.closest("[data-param-save]");
  if (paramSave) {
    await saveVehicleParameterFromRow(paramSave);
    return;
  }

  const button = event.target.closest("button");
  if (!button) return;

  if (button.dataset.command) {
    els.commandInput.value = button.dataset.command;
    els.commandInput.focus();
    return;
  }

  if (button.dataset.control) {
    await runButton(button, () => invokeFlightControl(button.dataset.control), "控制指令已执行");
    return;
  }

  if (button.dataset.tool) {
    const tool = button.dataset.tool;
    const params = parseParams(button.dataset.params);
    const targets = controlTargetList();
    await runButton(
      button,
      async () => {
        // 多选(或未选=全部)时:解锁/起飞逐台下发;起飞用非阻塞派发,多机同时升空
        if (targets.length > 1 && (tool === "drone_takeoff" || tool === "drone_arm")) {
          let last = null;
          for (const name of targets) {
            if (tool === "drone_takeoff") {
              last = await invokeFlightTool("drone_dispatch_takeoff", { ...params, vehicle_name: name });
            } else {
              last = await invokeFlightTool(tool, { ...params, vehicle_name: name });
            }
          }
          return last;
        }
        const targetParam = targets.length ? { vehicle_name: targets[0] } : {};
        return invokeFlightTool(tool, { ...params, ...targetParam });
      },
      `${tool} 已执行`,
    );
    return;
  }

  if (button.dataset.waypointAction) {
    handleWaypointAction(button.dataset.waypointAction, button);
    return;
  }

  if (button.dataset.zoom) {
    if (!maplibreMap) return;
    if (button.dataset.zoom === "in") maplibreMap.zoomIn();
    else maplibreMap.zoomOut();
  }
});

document.addEventListener("toggle", (event) => {
  const detail = event.target?.closest?.(".message-detail");
  if (!detail) return;
  const id = detail.dataset.detailId;
  if (!id) return;
  if (detail.open) openDetailIds.add(id);
  else openDetailIds.delete(id);
}, true);

els.settingsOpen.addEventListener("click", () => {
  openAgentSettings();
});
if (els.mapSettingsBtn) {
  els.mapSettingsBtn.addEventListener("click", () => {
    openSystemSettings();
  });
}
if (els.profileToggle) {
  els.profileToggle.addEventListener("click", () => {
    const collapsed = els.profileToggle.dataset.collapsed !== "true";
    els.profileToggle.dataset.collapsed = String(collapsed);
    document.body.dataset.profileCollapsed = String(collapsed);
    if (maplibreMap) maplibreMap.resize();
  });
}
if (els.agentSettingsClose) {
  els.agentSettingsClose.addEventListener("click", () => closeAgentSettings());
}
const SETTINGS_EXPAND_SVG = '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 6V2.5h3.5M13.5 6V2.5h-3.5M2.5 10v3.5h3.5M13.5 10v3.5h-3.5"/></svg>';
const SETTINGS_COLLAPSE_SVG = '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 2.5v3H2.5M10.5 2.5v3h3M5.5 13.5v-3H2.5M10.5 13.5v-3h3"/></svg>';
let settingsSavedSize = null;

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

if (els.systemSettingsClose) {
  els.systemSettingsClose.addEventListener("click", () => closeSystemSettings());
}
if (els.systemSettingsMaximize) {
  els.systemSettingsMaximize.innerHTML = SETTINGS_EXPAND_SVG;
  els.systemSettingsMaximize.addEventListener("click", toggleSystemSettingsMaximize);
}
setupSystemSettingsResize();
if (els.refreshFirmwareInfoBtn) {
  els.refreshFirmwareInfoBtn.addEventListener("click", async () => {
    els.refreshFirmwareInfoBtn.disabled = true;
    const original = els.refreshFirmwareInfoBtn.textContent;
    els.refreshFirmwareInfoBtn.textContent = "读取中...";
    try {
      await loadVehicleInfo(true);
      showNotice("固件信息已刷新", "success");
    } finally {
      els.refreshFirmwareInfoBtn.disabled = false;
      els.refreshFirmwareInfoBtn.textContent = original || "刷新固件信息";
    }
  });
}
if (els.refreshVehicleParametersBtn) {
  els.refreshVehicleParametersBtn.addEventListener("click", async () => {
    els.refreshVehicleParametersBtn.disabled = true;
    const original = els.refreshVehicleParametersBtn.textContent;
    els.refreshVehicleParametersBtn.textContent = "读取中...";
    try {
      await loadVehicleParameters(true);
      const status = vehicleParametersCache?.status || "";
      showNotice(status === "ready" ? "参数已刷新" : "参数读取未完整完成，已显示当前收到的数据", status === "ready" ? "success" : "info");
    } finally {
      els.refreshVehicleParametersBtn.disabled = false;
      els.refreshVehicleParametersBtn.textContent = original || "刷新参数";
    }
  });
}
if (els.vehicleParameterSearch) {
  els.vehicleParameterSearch.addEventListener("input", () => {
    if (vehicleParameterSearchTimer) clearTimeout(vehicleParameterSearchTimer);
    vehicleParameterSearchTimer = setTimeout(() => {
      loadVehicleParameters(false).catch(() => {});
    }, 220);
  });
}
if (els.settingsBackdrop) {
  els.settingsBackdrop.addEventListener("click", () => {
    closeAgentSettings();
    closeSystemSettings();
  });
}
els.newSessionBtn.addEventListener("click", () => createSession());
if (els.sessionNavBtn) {
  els.sessionNavBtn.addEventListener("click", () => {
    if (els.sessionsPanel.classList.contains("is-open")) closeSessionsPanel();
    else openSessionsPanel();
  });
}
if (els.currentSessionLabel) {
  els.currentSessionLabel.addEventListener("dblclick", startHeaderSessionRename);
}
els.addModelBtn.addEventListener("click", () => openModelModal());
if (els.modelModalClose) els.modelModalClose.addEventListener("click", closeModelModal);
if (els.modelModalCancel) els.modelModalCancel.addEventListener("click", closeModelModal);
if (els.modelRevealKey) {
  els.modelRevealKey.addEventListener("click", async () => {
    const modelId = els.modelEditId?.value.trim();
    if (!modelId) return;
    if (els.modelApiKey.type === "text") {
      els.modelApiKey.type = "password";
      els.modelApiKey.value = "";
      els.modelRevealKey.textContent = "显示";
      return;
    }
    try {
      const data = await post(`/api/models/${encodeURIComponent(modelId)}/reveal-key`, {});
      els.modelApiKey.value = data.api_key || "";
      els.modelApiKey.type = "text";
      els.modelRevealKey.textContent = "隐藏";
    } catch (error) {
      showNotice(error.message || "读取密钥失败", "error");
    }
  });
}
if (els.modelForm) {
  els.modelForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitModelForm();
  });
}

if (els.skillList) {
  els.skillList.addEventListener("click", (event) => {
    const item = event.target.closest("[data-skill-id]");
    if (!item) return;
    openSkillModal(item.dataset.skillId);
  });
}
if (els.skillModalClose) els.skillModalClose.addEventListener("click", closeSkillModal);
if (els.skillForm) {
  els.skillForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitSkillForm();
  });
}
if (els.addSkillBtn) els.addSkillBtn.addEventListener("click", openNewSkillModal);
if (els.importSkillBtn && els.skillImportInput) {
  els.importSkillBtn.addEventListener("click", () => els.skillImportInput.click());
  els.skillImportInput.addEventListener("change", async () => {
    const file = els.skillImportInput.files?.[0];
    els.skillImportInput.value = "";
    if (!file) return;
    try {
      const markdown = await file.text();
      const name = markdown.match(/^name:\s*([^\r\n]+)/m)?.[1]?.trim().replace(/^["']|["']$/g, "");
      if (!name) throw new Error("SKILL.md 缺少 name 字段");
      await post("/api/skills", { action: "create", id: name, markdown });
      await loadSkills(true);
      renderSkills();
      showNotice(`已导入 skill:${name.replace(/^skill:/, "")}`, "success");
    } catch (error) {
      showNotice(error.message || "Skill 导入失败", "error");
    }
  });
}

[els.wpPropType, els.wpPropAlt, els.wpPropSpeed, els.wpPropHold, els.wpPropAccept].forEach((input) => {
  if (!input) return;
  input.addEventListener("input", () => applyWaypointProperties());
  input.addEventListener("change", () => applyWaypointProperties());
});

// 地图右侧航点面板折叠
