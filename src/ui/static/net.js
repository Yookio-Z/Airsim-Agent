/* // 网络层与模型/技能加载：api()/post()、模型注册表、连接事件（顶层初始化调用已移入 init.js） */

function loadAutoConnectEnabled() {
  return autoConnectEnabled;
}

async function saveAutoConnectEnabled(enabled) {
  autoConnectEnabled = Boolean(enabled);
  await saveConnectionSettings();
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
      // 保险: 切换预设后强制再渲染一次模板, 避免被中间步骤覆盖
      renderAirSimSettingsForConnection();
    });
    els.connectionsList.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const item = event.target.closest(".connection-item");
      if (!item) return;
      event.preventDefault();
      const connId = item.dataset.connectionId;
      renderConnectionDetail(connId);
      renderAirSimSettingsForConnection();
    });
  }
}

async function loadSkills(force = false) {
  if (skillsLoaded && !force) return;
  try {
    const data = await api("/api/skills");
    skillsCache = data.skills || [];
    skillsLoaded = true;
  } catch (error) {
    skillsCache = [];
  }
}

async function fetchModels() {
  try {
    const data = await api("/api/models");
    if (data && Array.isArray(data.models)) {
      modelsCache = data.models.length ? data.models : [...DEFAULT_MODELS];
      backendDefaultModelId = data.default || "";
    }
  } catch (error) {
    showNotice("模型列表加载失败: " + (error.message || "未知错误"), "error");
  }
  renderModelSelector();
  return modelsCache;
}

function loadModels() {
  return modelsCache;
}

function getSelectedModelId() {
  const saved = localStorage.getItem("airsim-agent-model-selected");
  if (saved && modelsCache.some((m) => m.id === saved)) return saved;
  if (backendDefaultModelId && modelsCache.some((m) => m.id === backendDefaultModelId)) return backendDefaultModelId;
  return els.modelSelector.value || modelsCache[0]?.id || "deepseek";
}

function setSelectedModelId(id) {
  localStorage.setItem("airsim-agent-model-selected", id);
  els.modelSelector.value = id;
  renderModelSelector();
}

function renderModelSelector() {
  const models = loadModels();
  const selector = els.modelSelector;
  selector.innerHTML = "";
  models.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = m.name;
    selector.appendChild(opt);
  });
  const saved = getSelectedModelId();
  if (saved && models.some((m) => m.id === saved)) selector.value = saved;

  const selected = models.find((m) => m.id === selector.value) || models[0];
  if (selected && els.modelSelectorLabel) {
    const effortLabel = reasoningEffortLabel(selected);
    els.modelSelectorLabel.textContent = effortLabel ? `${selected.name} · ${effortLabel}` : selected.name;
  }
  renderModelMenu();
}

function reasoningEffortLabel(model) {
  const mode = model?.thinking_mode || "";
  const effort = model?.reasoning_effort || "";
  if (mode === "disabled") return "无思考";
  if (mode === "enabled" && !effort) return "思考";
  const names = { low: "低思考", medium: "中思考", high: "高思考", max: "最大思考" };
  return names[effort] || "";
}

function renderModelMenu() {
  if (!els.modelSelectorMenu) return;
  const models = loadModels();
  const selectedId = getSelectedModelId();
  const items = models.map((m) => `
    <button class="model-option ${m.id === selectedId ? "active" : ""}" data-model-id="${escapeHtml(m.id)}" type="button">
      <span>${escapeHtml(m.name)}${m.multimodal ? " · 视觉" : ""}</span>
      <span class="check">✓</span>
    </button>
  `).join("");
  const addButton = `
    <button class="model-option model-option-add" data-model-action="add" type="button">
      <span>＋ 添加模型</span>
    </button>
  `;
  els.modelSelectorMenu.innerHTML = items + addButton;
}

function toggleModelMenu() {
  if (!els.modelSelectorMenu) return;
  const hidden = els.modelSelectorMenu.hidden;
  closeAllDropdowns();
  els.modelSelectorMenu.hidden = !hidden;
  els.modelSelectorMenu.closest(".model-dropdown")?.classList.toggle("open", !hidden);
}

function closeAllDropdowns() {
  if (els.modelSelectorMenu) {
    els.modelSelectorMenu.hidden = true;
    els.modelSelectorMenu.closest(".model-dropdown")?.classList.remove("open");
  }
}

function onModelChange(id) {
  setSelectedModelId(id);
  closeAllDropdowns();
}

async function saveModelToBackend(payload) {
  const isEdit = Boolean(payload.id);
  if (isEdit) {
    const data = await post(`/api/models/${encodeURIComponent(payload.id)}`, payload);
    return data.model;
  }
  const data = await post("/api/models", payload);
  return data.model;
}

async function deleteModelFromBackend(modelId) {
  await post(`/api/models/${encodeURIComponent(modelId)}/delete`, {});
}

async function setDefaultModel(modelId) {
  await post("/api/models", { action: "default", id: modelId });
}

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

document.addEventListener("click", (event) => {
  const option = event.target.closest(".model-option");
  if (option) {
    const action = option.dataset.modelAction;
    if (action === "add") {
      event.stopPropagation();
      openModelModal();
      return;
    }
    const id = option.dataset.modelId;
    if (id) onModelChange(id);
    return;
  }
  if (!event.target.closest(".model-dropdown") && !event.target.closest("#modelModal")) {
    closeAllDropdowns();
  }
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

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(extractApiError(data, response.statusText));
  }
  if (data && data.ok === false) {
    throw new Error(extractApiError(data, "request failed"));
  }
  return data;
}

function post(path, payload) {
  return api(path, { method: "POST", body: JSON.stringify(payload) });
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

