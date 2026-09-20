// ui-main.js —— 启动引导与事件绑定（保持原顺序，最后执行）
// 由 app.js 拆分而来；各文件共享同一份脚本作用域，按顺序加载。

initLayoutPrefs();
initSplitters();
renderModelSelector();
fetchModels();
loadSkills();
setupConnectionEventListeners();
setupCameraEventListeners();
initSystemSettingsDrag();

// 异步加载后端连接设置（后端已在启动时根据 auto_connect 自动尝试连接）。
loadConnectionSettings();
loadCameraSettings();
loadApplicationSettings();

document.querySelectorAll(".application-settings-save").forEach((button) => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await saveApplicationSettings();
    } catch (error) {
      showNotice(error.message || "应用设置保存失败", "error");
    } finally {
      button.disabled = false;
    }
  });
});

els.modelSelector.addEventListener("change", () => onModelChange(els.modelSelector.value));

if (els.modelSelectorBtn) {
  els.modelSelectorBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleModelMenu();
  });
}
if (els.reasoningBtn) {
  els.reasoningBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleReasoningMenu();
  });
}
if (els.contextUsage) {
  els.contextUsage.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleContextPopover();
  });
}

document.addEventListener("click", (event) => {
  const option = event.target.closest(".model-option");
  if (option) {
    const action = option.dataset.modelAction;
    if (action === "add") {
      event.stopPropagation();
      closeComposerMenus();
      openModelModal();
      return;
    }
    const id = option.dataset.modelId;
    if (id) onModelChange(id);
    return;
  }
  const reasoningOption = event.target.closest("[data-reasoning-value], [data-reasoning-toggle]");
  if (reasoningOption) {
    event.stopPropagation();
    if (reasoningOption.dataset.reasoningToggle === "off") {
      closeComposerMenus();
      applyReasoningSetting({ thinking_mode: "disabled" }, "已关闭思考");
    } else {
      const value = reasoningOption.dataset.reasoningValue || "";
      closeComposerMenus();
      applyReasoningSetting(
        { thinking_mode: value ? "enabled" : "", reasoning_effort: value },
        value ? `思考力度已设为「${REASONING_LEVEL_META[value]?.label || value}」` : "思考力度已恢复模型默认"
      );
    }
    return;
  }
  const railTick = event.target.closest("[data-rail-message]");
  if (railTick) {
    event.stopPropagation();
    jumpToChatMessage(railTick.dataset.railMessage || "");
    return;
  }
  const insideOverlay = event.target.closest(
    ".composer-dropdown, #sessionSwitcher, #sessionMenu, #modelModal"
  );
  if (!insideOverlay) closeAllDropdowns();
});

document.addEventListener("click", async (event) => {
  const sessionAction = event.target.closest("[data-session-action]");
  if (!sessionAction) return;

  const sessionId = sessionAction.dataset.sessionId;
  const action = sessionAction.dataset.sessionAction;
  if (!sessionId || !action) return;

  event.stopPropagation();

  if (action === "load") {
    await loadSession(sessionId);
    return;
  }

  if (action === "rename") {
    startSessionRowRename(sessionAction.closest(".session-item"));
    return;
  }

  if (action === "load" && event.detail >= 2) {
    startSessionRowRename(sessionAction.closest(".session-item"));
    return;
  }

  if (action === "delete") {
    await deleteSession(sessionId);
    return;
  }

  if (action === "export") {
    const format = sessionAction.dataset.sessionFormat || "markdown";
    window.location.href = `/api/sessions/${encodeURIComponent(sessionId)}/export?format=${encodeURIComponent(format)}`;
    return;
  }
});

if (els.chatModeBtn) els.chatModeBtn.addEventListener("click", () => setCommandMode("chat"));
if (els.executeModeBtn) els.executeModeBtn.addEventListener("click", () => setCommandMode("execute"));
setCommandMode(commandMode);

if (commandSubmitButton) {
  commandSubmitButton.addEventListener("click", async (event) => {
    if (!isAgentWorkActive()) return;
    event.preventDefault();
    await cancelActiveWork();
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
    // 提交成功后不再同步 await refresh()：那会在乐观气泡刚上屏时触发一次
    // 整页重渲染（工具清单/事件流/会话与 Skill 列表/地图都是整段重建），
    // 正是"点发送后卡一下"的主因。对话与运行态由 SSE
    // （message_create/message_update/run_update）实时推进，这里只保留
    // 事件流不可用时的兜底同步。
    schedulePostSubmitFallback(resp?.run_id || "");
    if (resp && resp.ok) {
      if (resp.status === "steered") {
        // 任务已在执行：这条指令作为补充指令并入了正在跑的循环，而不是新任务
        showNotice("已作为补充指令并入当前任务执行", "success");
      } else {
        showNotice(mode === "execute" ? "任务已进入执行流程" : "Chat 已提交，正在生成回复", "success");
      }
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
    if (collapsed) missionProfileDrag = null;
    if (maplibreMap) maplibreMap.resize();
  });
}
setupMissionProfileInteraction();
if (els.agentSettingsClose) {
  els.agentSettingsClose.addEventListener("click", () => closeAgentSettings());
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
if (els.sessionsNewBtn) els.sessionsNewBtn.addEventListener("click", () => createSession());
if (els.sessionSwitcherBtn) {
  els.sessionSwitcherBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    if (renamingSession) return;
    toggleSessionMenu();
  });
}
if (els.chatRail) {
  // 悬浮即时显示记录预览（自绘卡片，不受原生 title 的 ~1s 延迟影响）
  els.chatRail.addEventListener("mouseover", (event) => {
    const tick = event.target.closest(".rail-tick");
    if (tick) showRailTip(tick);
  });
  els.chatRail.addEventListener("mouseleave", () => hideRailTip());
  els.chatRail.addEventListener("focusin", (event) => {
    const tick = event.target.closest(".rail-tick");
    if (tick) showRailTip(tick);
  });
  els.chatRail.addEventListener("focusout", () => hideRailTip());
  els.chatRail.addEventListener("scroll", () => hideRailTip(), { passive: true });
}
if (els.currentSessionLabel) {
  els.currentSessionLabel.addEventListener("dblclick", (event) => {
    event.preventDefault();
    event.stopPropagation();
    startHeaderSessionRename();
  });
}
if (els.sessionMenu) {
  els.sessionMenu.addEventListener("input", (event) => {
    if (event.target.id !== "sessionMenuSearch") return;
    setSessionFilter(event.target.value);
    // 重新渲染会重建搜索框，把光标放回末尾，避免连续输入掉字
    const search = document.getElementById("sessionMenuSearch");
    if (search) {
      search.focus();
      if (typeof search.setSelectionRange === "function") {
        const end = search.value.length;
        search.setSelectionRange(end, end);
      }
    }
  });
  els.sessionMenu.addEventListener("click", (event) => {
    const action = event.target.closest("[data-session-menu]")?.dataset.sessionMenu;
    if (!action) return;
    event.stopPropagation();
    if (action === "new") {
      createSession();
      return;
    }
    if (action === "all") {
      closeAllDropdowns();
      openSessionsPanel();
    }
  });
}
if (els.sessionsSearch) {
  els.sessionsSearch.addEventListener("input", () => setSessionFilter(els.sessionsSearch.value));
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
if (els.fetchModelListBtn) els.fetchModelListBtn.addEventListener("click", () => fetchProviderModelList());

if (els.modelForm) {
  els.modelForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitModelForm();
  });
}

if (els.eventList) {
  els.eventList.addEventListener("click", (event) => {
    const filter = event.target.closest("[data-event-level]");
    if (filter) {
      eventLevelFilter = filter.dataset.eventLevel || "all";
      renderEvents(latestState?.events || []);
      return;
    }
    const item = event.target.closest("[data-event-details]");
    const details = item?.querySelector(".event-data");
    if (details) details.hidden = !details.hidden;
  });
}

if (els.skillList) {
  els.skillList.addEventListener("input", (event) => {
    if (event.target.id === "skillSearchInput") setSkillFilter(event.target.value);
  });
  els.skillList.addEventListener("click", (event) => {
    const actionEl = event.target.closest("[data-skill-action]");
    if (!actionEl) return;
    const item = actionEl.closest("[data-skill-id]");
    const skill = item ? skillsCache.find((s) => (s.id || s.name) === item.dataset.skillId) : null;
    const action = actionEl.dataset.skillAction;
    if (!skill) return;
    event.stopPropagation();
    if (action === "toggle") toggleSkillEnabled(skill);
    else if (action === "duplicate") duplicateSkill(skill);
    else if (action === "delete") deleteSkill(skill);
  });
}
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

if (waypointPanelToggle && waypointPanel) {
  waypointPanelToggle.addEventListener("click", () => {
    const collapsed = waypointPanel.dataset.collapsed !== "true";
    waypointPanel.dataset.collapsed = String(collapsed);
    waypointPanelToggle.textContent = collapsed ? "⌄" : "⌃";
  });
}

els.canvas = document.querySelector("#missionMap");
initMissionMap();

refresh().catch((error) => showNotice(error.message || "状态加载失败", "error"));

normalizeAgentSettingsCopy();
normalizeSystemSettingsCopy();

connectEventStream();

renderInitialDefaults();

initialRefresh();

restartMainTelemetryRefresh();
setInterval(() => refresh().catch(() => {}), 6000);
window.addEventListener("resize", () => {
  if (maplibreMap) maplibreMap.resize();
});
