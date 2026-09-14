// ui-composer.js —— 输入框、模式切换、模型/思考力度/上下文控件
// 由 app.js 拆分而来；各文件共享同一份脚本作用域，按顺序加载。

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
    els.modelSelectorLabel.textContent = selected.name;
    els.modelSelectorBtn.title = `当前模型：${selected.name}${selected.multimodal ? "（支持图像输入）" : ""}`;
  }
  renderModelMenu();
  renderReasoningChip();
}

function selectedModel() {
  return loadModels().find((m) => m.id === getSelectedModelId()) || loadModels()[0] || null;
}

function reasoningCapability(model) {
  const profile = model?.reasoning || {};
  const levels = Array.isArray(profile.levels)
    ? profile.levels.filter((level) => REASONING_LEVEL_META[level])
    : [];
  return {
    supports: Boolean(profile.supports_thinking) || levels.length > 0,
    levels,
    default: REASONING_LEVEL_META[profile.default] ? profile.default : "",
    mandatory: Boolean(profile.mandatory),
  };
}

function reasoningLevelOf(model) {
  if (String(model?.thinking_mode || "").toLowerCase() === "disabled") return "off";
  const effort = String(model?.reasoning_effort || "");
  return REASONING_LEVEL_META[effort] ? effort : "";
}

function reasoningEffortLabel(model) {
  const level = reasoningLevelOf(model);
  if (level === "off") return "无思考";
  return REASONING_LEVEL_META[level]?.label || "默认";
}

function reasoningBars(level) {
  if (level === "off") return 0;
  return REASONING_LEVEL_META[level]?.bars || 0;
}

function renderReasoningChip() {
  const model = selectedModel();
  const capability = reasoningCapability(model);
  const level = model ? reasoningLevelOf(model) : "";
  const meter = els.reasoningLabel;
  if (meter) {
    meter.dataset.level = level === "off" ? "off" : String(reasoningBars(level));
    meter.classList.toggle("is-off", level === "off");
  }
  if (els.reasoningBtn) {
    const unsupported = Boolean(model) && !capability.supports;
    els.reasoningBtn.classList.toggle("is-unsupported", unsupported);
    els.reasoningBtn.disabled = unsupported;
    els.reasoningBtn.title = !model
      ? "思考力度"
      : unsupported
        ? `${model.name}：未识别到可调档位（保持模型默认）`
        : `${model.name} · 思考力度：${reasoningEffortLabel(model)}`;
  }
  renderReasoningMenu();
}

function renderReasoningMenu() {
  if (!els.reasoningMenu) return;
  const model = selectedModel();
  const capability = reasoningCapability(model);
  const level = model ? reasoningLevelOf(model) : "";
  const levelRow = (value, label) => `
    <button class="composer-menu-item ${level === value ? "active" : ""}" data-reasoning-value="${escapeHtml(value)}" type="button" role="menuitemradio" aria-checked="${level === value}" title="${escapeHtml(label)}">
      <span class="level-meter" data-level="${reasoningBars(value)}" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
      <span class="composer-menu-main">${escapeHtml(label)}</span>
      <span class="check">✓</span>
    </button>
  `;
  const rows = [
    levelRow("", "模型默认"),
    ...capability.levels.map((value) => levelRow(value, REASONING_LEVEL_META[value].label)),
  ].join("");
  const offRow = capability.mandatory
    ? ""
    : `
    <div class="composer-menu-sep"></div>
    <button class="composer-menu-item ${level === "off" ? "active" : ""}" data-reasoning-toggle="off" type="button" role="menuitemcheckbox" aria-checked="${level === "off"}" title="不发送思考参数">
      <span class="level-meter" data-level="off" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
      <span class="composer-menu-main">关闭思考</span>
      <span class="check">✓</span>
    </button>`;
  els.reasoningMenu.innerHTML = `
    <div class="composer-menu-head">思考力度${model ? ` · ${escapeHtml(model.name)}` : ""}</div>
    ${capability.supports ? rows : `<div class="composer-menu-empty">未识别到可调档位，保持模型默认</div>`}
    ${offRow}
  `;
}

async function applyReasoningSetting(updates, message) {
  const model = selectedModel();
  if (!model?.id) return;
  try {
    const data = await post(`/api/models/${encodeURIComponent(model.id)}`, { ...updates, name: model.name });
    const updated = data?.model;
    if (updated) {
      const index = modelsCache.findIndex((m) => m.id === updated.id);
      if (index >= 0) modelsCache[index] = { ...modelsCache[index], ...updated };
      else modelsCache.push(updated);
    }
    renderModelSelector();
    if (message) showNotice(message, "success");
  } catch (error) {
    showNotice(`思考力度保存失败: ${error.message || "未知错误"}`, "error");
  }
}

function renderModelMenu() {
  if (!els.modelSelectorMenu) return;
  const models = loadModels();
  const selectedId = getSelectedModelId();
  const groups = new Map();
  models.forEach((m) => {
    const key = String(m.provider || "其他");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(m);
  });
  const modelRows = [...groups.entries()].map(([provider, items]) => `
    <div class="composer-menu-group">${escapeHtml(provider)}</div>
    ${items.map((m) => {
      const badges = [
        m.multimodal ? "视觉" : "文本",
        reasoningEffortLabel(m) || "",
      ].filter(Boolean);
      return `
        <button class="composer-menu-item model-option ${m.id === selectedId ? "active" : ""}" data-model-id="${escapeHtml(m.id)}" type="button" role="option" aria-selected="${m.id === selectedId}">
          <span class="composer-menu-main">${escapeHtml(m.name)}</span>
          <span class="composer-menu-badges">${badges.map((b) => `<span class="composer-badge">${escapeHtml(b)}</span>`).join("")}</span>
          <span class="check">✓</span>
        </button>
      `;
    }).join("")}
  `).join("");
  els.modelSelectorMenu.innerHTML = `
    <div class="composer-menu-head">选择模型</div>
    <div class="model-menu-list">${modelRows || '<div class="composer-menu-empty">还没有可用模型</div>'}</div>
    <div class="composer-menu-sep"></div>
    <button class="composer-menu-item model-option model-option-add" data-model-action="add" type="button">
      <span class="composer-menu-main">＋ 添加模型</span>
    </button>
  `;
}

function closeComposerMenus(except = null) {
  [
    [els.modelSelectorMenu, els.modelDropdown, els.modelSelectorBtn],
    [els.reasoningMenu, els.reasoningDropdown, els.reasoningBtn],
    [els.contextPopover, els.contextDropdown, els.contextUsage],
  ].forEach(([menu, dropdown, button]) => {
    if (!menu || menu === except) return;
    menu.hidden = true;
    dropdown?.classList.remove("open");
    button?.setAttribute("aria-expanded", "false");
  });
}

function toggleComposerMenu(menu, dropdown, button, render) {
  if (!menu) return;
  const willOpen = menu.hidden;
  closeComposerMenus(willOpen ? menu : null);
  if (willOpen && typeof render === "function") render();
  menu.hidden = !willOpen;
  dropdown?.classList.toggle("open", willOpen);
  button?.setAttribute("aria-expanded", String(willOpen));
}

function toggleModelMenu() {
  toggleComposerMenu(els.modelSelectorMenu, els.modelDropdown, els.modelSelectorBtn, renderModelMenu);
}

function toggleReasoningMenu() {
  toggleComposerMenu(els.reasoningMenu, els.reasoningDropdown, els.reasoningBtn, renderReasoningMenu);
}

function toggleContextPopover() {
  toggleComposerMenu(els.contextPopover, els.contextDropdown, els.contextUsage, renderContextPopover);
}

function onModelChange(id) {
  setSelectedModelId(id);
  // Switching now also updates the backend default, so the selected model is
  // used consistently by planning, attachments, and subsequent turns.
  setDefaultModel(id).catch(() => {});
  closeComposerMenus();
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

function setCommandMode(mode) {
  commandMode = mode === "execute" ? "execute" : "chat";
  localStorage.setItem("airsim-agent-command-mode", commandMode);
  [
    [els.chatModeBtn, "chat"],
    [els.executeModeBtn, "execute"],
  ].forEach(([button, value]) => {
    if (!button) return;
    const active = commandMode === value;
    button.classList.toggle("active", active);
    button.setAttribute("aria-checked", String(active));
  });
  els.commandForm?.classList.toggle("mode-execute", commandMode === "execute");
  if (els.commandInput) {
    els.commandInput.placeholder = commandMode === "execute"
      ? "Execute a flight command..."
      : "Chat, ask status, or clarify a plan...";
  }
  const submitButton = els.commandForm?.querySelector("button[type='submit']");
  if (submitButton) {
    submitButton.classList.toggle("execute", commandMode === "execute");
    submitButton.title = commandMode === "execute" ? "执行任务 (Enter)" : "发送聊天 (Enter)";
    submitButton.setAttribute("aria-label", commandMode === "execute" ? "执行任务" : "发送");
  }
  syncCommandSubmitState();
}

function getCommandSubmitButton() {
  return els.commandForm?.querySelector("button[type='submit']") || null;
}

function syncCommandSubmitState() {
  const submitButton = getCommandSubmitButton();
  if (!submitButton) return;
  const active = isAgentWorkActive();
  submitButton.classList.toggle("execute", commandMode === "execute" && !active);
  submitButton.classList.toggle("busy", active);
  submitButton.disabled = false;
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

// 向厂商要"当前实际有哪些模型"：模型命名会随版本变（DeepSeek 就把
// deepseek-chat/reasoner 换成了 deepseek-v4-*），所以以官方目录为准。
async function fetchProviderModelList() {
  const btn = els.fetchModelListBtn;
  const hint = els.providerModelHint;
  const baseUrl = els.modelBaseUrl.value.trim();
  const apiKey = els.modelApiKey.value.trim();
  if (!baseUrl) {
    showNotice("先填 Base URL，再拉取厂商模型列表", "error");
    return;
  }
  if (btn) { btn.disabled = true; btn.textContent = "拉取中..."; }
  if (hint) hint.textContent = "正在读取厂商模型目录...";
  try {
    const data = await post("/api/models/catalog", {
      base_url: baseUrl,
      api_type: els.modelApiType.value,
      api_key: apiKey,
    });
    const models = Array.isArray(data.models) ? data.models : [];
    if (els.providerModelOptions) {
      els.providerModelOptions.innerHTML = models
        .map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml([m.id, m.context_length ? `${Math.round(m.context_length / 1000)}k` : "", (m.reasoning_levels || []).length ? `思考 ${m.reasoning_levels.join("/")}` : ""].filter(Boolean).join(" · "))}</option>`)
        .join("");
    }
    if (hint) {
      hint.textContent = models.length
        ? `已从厂商目录读到 ${models.length} 个模型，输入框下拉可直接选（如 ${models.slice(0, 3).map((m) => m.id).join("、")}${models.length > 3 ? " …" : ""}）`
        : "厂商目录里没有模型，请检查 Base URL 与 API Key。";
    }
    if (models.length && !els.modelModelId.value.trim()) {
      els.modelModelId.value = models[0].id;
      if (!els.modelName.value.trim()) els.modelName.value = els.modelName.placeholder ? models[0].id : els.modelName.value;
    }
    showNotice(models.length ? `读到 ${models.length} 个可用模型` : "厂商目录为空", models.length ? "success" : "error");
  } catch (error) {
    const detail = String(error?.data?.message || error?.message || "").trim();
    if (hint) hint.textContent = detail || "读取厂商目录失败，可手动填写模型 ID";
    showNotice(detail || "读取厂商目录失败", "error");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "拉取模型列表"; }
  }
}

function contextUsageData(memory) {
  const usage = memory?.conversation || {};
  const percentRaw = Number(usage.context_percent);
  const used = Number(usage.estimated_context_tokens);
  const total = Number(usage.context_window);
  const hasNumbers = Number.isFinite(used) && Number.isFinite(total) && total > 0;
  return {
    usage,
    percent: Number.isFinite(percentRaw) ? percentRaw : null,
    used: hasNumbers ? used : null,
    total: hasNumbers ? total : null,
    remaining: hasNumbers ? Math.max(0, total - used) : null,
    modelId: String(usage.model_id || ""),
    messagesSent: Number(usage.messages_sent_to_model),
    messagesSaved: Number(usage.messages_saved),
  };
}

function renderContextPopover() {
  if (!els.contextPopover) return;
  const data = contextUsageData(latestState?.memory);
  const pctText = data.percent == null ? "--" : `${data.percent.toFixed(data.percent >= 10 ? 0 : 1)}%`;
  const rows = [
    ["已用上下文", data.used == null ? "--" : `${formatTokens(data.used)} tokens`],
    ["模型窗口", data.total == null ? "--" : `${formatTokens(data.total)} tokens`],
    ["剩余空间", data.remaining == null ? "--" : `${formatTokens(data.remaining)} tokens`],
    ["发送给模型", Number.isFinite(data.messagesSent) ? `${data.messagesSent} 条消息` : "--"],
    ["会话已保存", Number.isFinite(data.messagesSaved) ? `${data.messagesSaved} 条消息` : "--"],
    ["计量模型", data.modelId || "--"],
  ];
  const level = data.percent == null ? "" : data.percent >= 90 ? "danger" : data.percent >= 70 ? "warn" : "ok";
  const barWidth = data.percent == null ? 0 : Math.max(1, Math.min(100, data.percent));
  els.contextPopover.innerHTML = `
    <div class="composer-menu-head">上下文用量</div>
    <div class="context-meter ${level}">
      <div class="context-meter-bar"><span style="width:${barWidth}%"></span></div>
      <div class="context-meter-value">${escapeHtml(pctText)}</div>
    </div>
    <div class="context-rows">
      ${rows.map(([label, value]) => `<div class="context-row"><span>${escapeHtml(label)}</span><b>${escapeHtml(String(value))}</b></div>`).join("")}
    </div>
    <div class="composer-menu-foot">${
      data.percent == null
        ? "等待模型上下文统计。"
        : data.percent >= 90
          ? "接近上限：Agent 会压缩较早的历史，完整会话仍会保存。"
          : "超出窗口后 Agent 会压缩较早的历史，完整会话仍会保存在会话文件里。"
    }</div>
  `;
}

function renderContextUsage(memory) {
  if (!els.contextUsage) return;
  const data = contextUsageData(memory ?? latestState?.memory);
  const visible = applicationSettings.agent.show_context_usage !== false;
  els.contextUsage.hidden = !visible;
  if (!visible) return;
  const pctText = data.percent == null ? "--" : `${data.percent.toFixed(data.percent >= 10 ? 0 : 1)}%`;
  els.contextUsage.textContent = "◔";
  els.contextUsage.title = data.used == null
    ? "等待模型上下文统计"
    : `上下文 ${data.used.toLocaleString()} / ${data.total.toLocaleString()} tokens（${pctText}）· 点击查看明细`;
  els.contextUsage.classList.toggle("warn", data.percent >= 70 && data.percent < 90);
  els.contextUsage.classList.toggle("danger", data.percent >= 90);
  if (els.contextPopover && !els.contextPopover.hidden) renderContextPopover();
}

// 窄面板压缩：不换行，改为收缩/隐藏文字（按面板宽度切换 compact/tight）
function syncComposerDensity() {
  const panel = document.getElementById("agentColumn");
  if (!panel) return;
  const w = panel.getBoundingClientRect().width;
  panel.classList.toggle("compact", w < 460);
  panel.classList.toggle("tight", w < 380);
}

// 思考节点：统一外壳 + 思考正文（长文折叠）
function reasoningNode(item, isLive) {
  const node = nodeShell("reasoning", isLive ? "running" : "");
  const title = document.createElement("span");
  title.className = "tl-node-title";
  title.textContent = item.title && item.title !== "模型思考" ? ` · ${item.title}` : "";
  node.querySelector(".tl-node-head").appendChild(title);
  const text = String(item.body || "");
  if (isLive) {
    const pre = document.createElement("pre");
    pre.className = "tl-think-body";
    pre.textContent = text || "思考中…";
    node.appendChild(pre);
  } else {
    node.appendChild(collapsibleText(text || "（无内容）", "tl-think-result"));
  }
  return node;
}


function renderMessageAttachments(attachments) {
  if (!Array.isArray(attachments) || !attachments.length) return "";
  const images = attachments.map((item) => {
    const src = String(item.url || item.data_url || "");
    if (!(src.startsWith("/api/attachments/") || src.startsWith("data:image/"))) return "";
    return `<img src="${escapeHtml(src)}" alt="${escapeHtml(item.name || "attached image")}" loading="lazy">`;
  }).filter(Boolean).join("");
  return images ? `<div class="message-images">${images}</div>` : "";
}

function openModelModal(modelId) {
  closeAllDropdowns();
  const model = modelId ? modelsCache.find((m) => m.id === modelId) : null;
  els.modelEditId.value = model ? model.id : "";
  els.modelModalTitle.textContent = model ? "编辑模型" : "添加模型";
  els.modelName.value = model ? model.name || "" : "";
  els.modelModelId.value = model ? model.model || "" : "";
  els.modelApiType.value = model ? model.api_type || "openai" : "openai";
  els.modelBaseUrl.value = model ? model.base_url || "" : "";
  els.modelApiKey.value = "";
  els.modelApiKey.type = "password";
  els.modelApiKey.placeholder = model?.key_hint
    ? `已保存 ${model.key_hint}，留空保持不变`
    : "输入 API Key";
  if (els.providerModelOptions) els.providerModelOptions.innerHTML = "";
  if (els.providerModelHint) {
    els.providerModelHint.textContent = "厂商会更新模型命名，这里以厂商当前实际提供的清单为准。";
  }
  if (els.modelRevealKey) {
    els.modelRevealKey.hidden = !model?.enabled;
    els.modelRevealKey.textContent = "显示";
  }
  els.modelModal.hidden = false;
}

function closeModelModal() {
  els.modelModal.hidden = true;
  if (els.modelApiKey) {
    els.modelApiKey.type = "password";
    els.modelApiKey.value = "";
  }
  if (els.modelRevealKey) els.modelRevealKey.textContent = "显示";
  if (els.modelForm) els.modelForm.reset();
}

function capabilitySourceLabel(source) {
  if (source === "manual") return "手动指定";
  if (source === "provider_catalog") return "厂商目录";
  return "按模型 ID 推断";
}

// "检测能力"失败时给出可操作的说明：厂商目录里到底有哪些模型 ID，
// 以及当前能力是从哪来的——避免只丢一句英文错误让人无从下手。
function detectFailureMessage(modelId, error) {
  const model = modelsCache.find((m) => m.id === modelId);
  const name = model?.name || modelId;
  const detail = String(error?.data?.message || error?.message || "").trim();
  const available = Array.isArray(error?.data?.available_models) ? error.data.available_models : [];
  const total = Number(error?.data?.available_total || available.length);
  const current = model ? capabilityDisplay(model) : "";
  const parts = [detail || `无法从厂商目录识别「${name}」的能力`];
  if (available.length) {
    const shown = available.slice(0, 6).join("、");
    parts.push(`该目录里有 ${total} 个模型，例如：${shown}${total > available.length ? " …" : ""}。请核对模型 ID 是否与厂商一致。`);
  } else {
    parts.push("该厂商目录没有列出这个模型，或目录需要有效的 API Key 才能读取。");
  }
  if (current) parts.push(`当前能力仍按已有信息识别为：${current}，不影响使用。`);
  return parts.join(" ");
}

function capabilityDisplay(model) {
  const modes = (model?.input_modes || []).join(" + ") || "文本";
  const context = Number(model?.context_window);
  const contextText = Number.isFinite(context) && context > 0 ? ` · 上下文 ${Math.round(context / 1000)}k` : "";
  return `${modes}（${capabilitySourceLabel(model?.capability_source)}）${contextText}`;
}

// 思考档位是自动识别的，列表里直接给结论，不需要用户配置
function reasoningSummary(model) {
  const reasoning = model?.reasoning || {};
  if (Array.isArray(reasoning.levels) && reasoning.levels.length) {
    return reasoning.levels.map((lv) => REASONING_LEVEL_META[lv]?.label || lv).join(" / ");
  }
  if (reasoning.supports_thinking) return "仅支持开关思考";
  // 没查到任何依据 ≠ 模型不支持，别让用户误判
  return reasoning.recognized === false ? "未能识别（按模型默认）" : "不支持";
}

function capabilityNotice(model) {
  if (!model) return "";
  const parts = [capabilityDisplay(model)];
  const reasoning = model.reasoning || {};
  if (Array.isArray(reasoning.levels) && reasoning.levels.length) {
    parts.push(`思考档位 ${reasoning.levels.map((lv) => REASONING_LEVEL_META[lv]?.label || lv).join("/")}`);
  } else if (reasoning.supports_thinking) {
    parts.push("支持开关思考");
  }
  if (model.context_window) parts.push(`上下文 ${Math.round(Number(model.context_window) / 1000)}k`);
  return `，已识别：${parts.join(" · ")}`;
}

async function submitModelForm() {
  const isEdit = Boolean(els.modelEditId.value.trim());
  // 用户只提供名称 / API 类型 / Base URL / 模型 ID / Key，
  // provider 与输入能力、思考档位都由服务端探测后自动写入。
  const payload = {
    id: els.modelEditId.value.trim(),
    name: els.modelName.value.trim(),
    model: els.modelModelId.value.trim(),
    api_type: els.modelApiType.value,
    base_url: els.modelBaseUrl.value.trim(),
  };
  const apiKey = els.modelApiKey.value.trim();
  if (apiKey || !isEdit) {
    payload.api_key = apiKey;
  }
  if (!payload.name || !payload.model) {
    showNotice("请填写名称和模型 ID", "error");
    return;
  }
  try {
    const saved = await saveModelToBackend(payload);
    await fetchModels();
    closeModelModal();
    if (els.agentSettingsDrawer && !els.agentSettingsDrawer.hidden) {
      renderModelConfig();
    }
    showNotice(`${isEdit ? "模型已更新" : "模型已添加"}${capabilityNotice(saved)}`, "success");
  } catch (error) {
    showNotice(error.message || "保存模型失败", "error");
  }
}

function renderModelConfig() {
  const models = loadModels();
  const list = document.getElementById("modelConfigList");
  if (!models.length) {
    list.innerHTML = `<div class="empty">暂无模型配置</div>`;
    return;
  }

  list.innerHTML = models.map((m) => `
    <div class="model-config-item" data-model-id="${escapeHtml(m.id)}">
      <div class="config-row">
        <label>名称</label>
        <span class="config-value">${escapeHtml(m.name)}</span>
      </div>
      <div class="config-row">
        <label>Provider</label>
        <span class="config-value">${escapeHtml(m.provider)}</span>
      </div>
      <div class="config-row">
        <label>Model</label>
        <span class="config-value">${escapeHtml(m.model)}</span>
      </div>
      <div class="config-row">
        <label>API 类型</label>
        <span class="config-value">${escapeHtml(m.api_type || "openai")}</span>
      </div>
      <div class="config-row">
        <label>Base URL</label>
        <span class="config-value">${escapeHtml(m.base_url || "—")}</span>
      </div>
      <div class="config-row">
        <label>状态</label>
        <span class="config-value ${m.enabled ? "enabled" : "disabled"}">${m.enabled ? `已配置 ${escapeHtml(m.key_hint || "Key")}` : "未配置 Key"}</span>
      </div>
      <div class="config-row">
        <label>自动识别</label>
        <span class="config-value">${escapeHtml(capabilityDisplay(m))}</span>
      </div>
      <div class="config-row">
        <label>思考档位</label>
        <span class="config-value">${escapeHtml(reasoningSummary(m))}</span>
      </div>
      <div class="config-actions">
        <button class="edit-model" data-action="edit" data-model-id="${escapeHtml(m.id)}">编辑</button>
        <button class="detect-model" data-action="detect" data-model-id="${escapeHtml(m.id)}">检测能力</button>
        <button class="delete-model" data-action="delete" data-model-id="${escapeHtml(m.id)}">删除</button>
      </div>
    </div>
  `).join("");

  list.querySelectorAll("[data-action='edit']").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      const modelId = event.target.closest("[data-model-id]").dataset.modelId;
      openAgentSettings();
      setSettingsTab("llm", els.agentSettingsDrawer);
      openModelModal(modelId);
    });
  });

  list.querySelectorAll("[data-action='detect']").forEach((btn) => {
    btn.addEventListener("click", async (event) => {
      const modelId = event.target.closest("[data-model-id]").dataset.modelId;
      if (!modelId) return;
      try {
        const data = await post(`/api/models/${encodeURIComponent(modelId)}/detect`, {});
        await fetchModels();
        renderModelConfig();
        const note = data.catalog_metadata === false
          ? "厂商目录只给了模型 ID，其余能力按模型 ID 推断"
          : "已按厂商目录更新";
        showNotice(`检测完成：${capabilityDisplay(data.model)} · ${note}`, "success");
      } catch (error) {
        showNotice(detectFailureMessage(modelId, error), "error");
      }
    });
  });

  list.querySelectorAll("[data-action='delete']").forEach((btn) => {
    btn.addEventListener("click", async (event) => {
      const modelId = event.target.closest("[data-model-id]").dataset.modelId;
      if (!modelId) return;
      if (modelsCache.length <= 1) {
        showNotice("至少保留一个模型配置", "error");
        return;
      }
      try {
        await deleteModelFromBackend(modelId);
        await fetchModels();
        renderModelConfig();
      } catch (error) {
        showNotice(error.message || "删除模型失败", "error");
      }
    });
  });
}

function backendModeView(toolRuntime = {}) {
  const profile = toolRuntime.backend_profile || {};
  const capabilities = profile.capabilities || {};
  const mode = profile.mode || profile.id || toolRuntime.backend || "unknown";
  const settings = profile.agent_settings || {};
  if (mode === "px4_ros2") {
    return {
      title: "PX4 ROS2 网关模式",
      body: "系统通过 HTTP 连接 ROS Provider Gateway；网关运行在 WSL 或机载计算机上，负责 ROS2 话题并通过 PX4 /fmu 话题闭环。",
      tags: [
        "ROS2 网关",
        profile.requires_ros_gateway ? "必须连接网关" : "网关可选",
        settings.ros_gateway_url || capabilities.ros_bridge_url || "http://127.0.0.1:8766",
        settings.ros_workspace || "$HOME/ws_px4",
      ],
    };
  }
  if (mode === "px4_mavlink") {
    return {
      title: "PX4 MAVLink 模式",
      body: "Agent 仅通过 MAVLink 控制 PX4；此模式不会调用 ROS Provider、ROS 避障适配器或 ROS 规划适配器。",
      tags: ["MAVLink", "不使用 ROS 工具", "遥测与指令", "SITL 或真实飞控"],
    };
  }
  if (mode === "airsim") {
    return {
      title: "纯 AirSim 模式",
      body: "Agent 通过 AirSim RPC 使用仿真、相机、深度与本地感知能力；该控制链路不经过 PX4 或 ROS。",
      tags: ["AirSim RPC", "相机", "深度", "仅仿真"],
    };
  }
  return {
    title: "后端模式",
    body: profile.control_path || profile.description || "Backend capabilities are loaded from the runtime profile.",
    tags: [mode, profile.control_path || "", profile.requires_ros_gateway ? "必须连接网关" : ""].filter(Boolean),
  };
}

function renderBackendModeNote(toolRuntime = {}) {
  const view = backendModeView(toolRuntime);
  return `
    <section class="settings-architecture-note">
      <strong>${escapeHtml(view.title)}</strong>
      <p>${escapeHtml(view.body)}</p>
      <div class="tool-meta subtle">
        ${view.tags.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
      </div>
    </section>
  `;
}

function normalizeSkillTitle(skill) {
  const display = String(skill?.display_name || skill?.id || skill?.name || "");
  return display.startsWith("skill:") ? display.slice("skill:".length) : display;
}

// 只做"已安装了什么"的清单：不在这里编辑内容，要改就去改文件
function renderSkills() {
  normalizeAgentSettingsCopy();
  if (!els.skillList) return;
  const skills = Array.isArray(skillsCache) ? skillsCache : [];
  const active = skills.filter(skillEnabled).length;
  els.skillCount.textContent = `${active} / ${skills.length}`;

  if (!skills.length) {
    els.skillList.innerHTML = `
      <div class="skill-empty">
        <strong>还没有安装 Skill</strong>
        <p>Skill 是写给 Agent 的操作规程，装好后它遇到对应任务会自动照着做，你不需要手动指定。</p>
        <p>把 SKILL.md 放到 <code>skills/&lt;名字&gt;/SKILL.md</code>，或点右上角「导入」。</p>
      </div>`;
    return;
  }

  const visible = filteredSkills();
  els.skillList.innerHTML = `
    <label class="skill-search">
      <input id="skillSearchInput" type="search" placeholder="搜索 Skill" value="${escapeHtml(skillFilter)}" autocomplete="off">
    </label>
    <div class="skill-rows">
      ${visible.length ? visible.map((skill) => {
        const id = skill.id || skill.name || "";
        const title = normalizeSkillTitle(skill);
        const enabled = skillEnabled(skill);
        const desc = String(skill.purpose || skill.description || "").replace(/\s+/g, " ").trim();
        const location = String(skill.doc_path_rel || "").trim();
        return `
        <article class="skill-row ${enabled ? "" : "disabled"}" data-skill-id="${escapeHtml(id)}">
          <div class="skill-row-main">
            <div class="skill-row-head">
              <strong>${escapeHtml(title)}</strong>
              <span class="skill-status ${enabled ? "on" : "off"}">${enabled ? "已安装" : "已停用"}</span>
            </div>
            ${desc ? `<p>${escapeHtml(desc)}</p>` : ""}
            <div class="skill-row-path">${location ? `<code>${escapeHtml(location)}</code>` : "<code>内置（runtime）</code>"}</div>
          </div>
          <div class="skill-row-actions">
            <button type="button" data-skill-action="toggle" title="${enabled ? "停用后 Agent 不再使用它" : "重新启用"}">${enabled ? "停用" : "启用"}</button>
            <button type="button" class="danger-text" data-skill-action="delete" title="删除这个 Skill 文件">删除</button>
          </div>
        </article>`;
      }).join("") : `<div class="empty small">没有匹配的 Skill</div>`}
    </div>
    <p class="skill-foot">要新增或修改内容，直接编辑上面的文件（或点「导入」替换），改动会自动加载。</p>`;
}

function skillDocStatus(skill) {
  return String(skill?.doc_status || "").toLowerCase();
}

function skillEnabled(skill) {
  return skill?.enabled !== false && !["disabled", "archived"].includes(skillDocStatus(skill));
}

function skillCostRiskLabel(skill) {
  const cost = String(skill?.cost || "").trim();
  const risk = String(skill?.risk || "").trim();
  const money = { low: "$", medium: "$$", high: "$$$" }[cost] || "";
  const riskText = { low: "低风险", medium: "中风险", high: "高风险" }[risk] || "";
  return [money && `成本 ${money}`, riskText].filter(Boolean).join(" · ");
}

function skillChips(skill) {
  const chips = [];
  const caps = Array.isArray(skill?.required_capabilities) ? skill.required_capabilities : [];
  const tools = Array.isArray(skill?.subtools) ? skill.subtools : [];
  if (!caps.length && !tools.length) chips.push("无特殊依赖");
  caps.forEach((cap) => chips.push(String(cap)));
  if (tools.length) chips.push(`${tools.length} 个工具`);
  return chips;
}

function filteredSkills() {
  const skills = Array.isArray(skillsCache) ? skillsCache : [];
  const term = skillFilter.trim().toLowerCase();
  if (!term) return skills;
  return skills.filter((s) => [s.id, s.name, s.display_name, s.description, s.purpose]
    .some((value) => String(value || "").toLowerCase().includes(term)));
}

function setSkillFilter(value) {
  skillFilter = String(value ?? "");
  renderSkills();
}


// SKILL.md 的 frontmatter <-> 表单字段互转：用户填字段，也能随时切回原文




// 展开"原文"时把表单内容同步进去，避免用户以为两种编辑各存一份





// 停用/启用：改写 frontmatter 的 status（registry 据此把该 Skill 排除在
// 模型可见的指导之外），type 保持原值不动。
function setSkillDocStatus(markdown, enabled) {
  const lines = String(markdown || "").split("\n");
  if (lines[0]?.trim() !== "---") return null;
  const end = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
  if (end < 0) return null;
  let statusIndex = -1;
  let typeValue = "";
  for (let i = 1; i < end; i += 1) {
    const status = /^\s*status\s*:\s*(.*)$/.exec(lines[i]);
    if (status) {
      statusIndex = i;
      const current = status[1].trim();
      if (current && !["disabled", "archived"].includes(current)) typeValue = current;
    }
    const type = /^\s*type\s*:\s*(.*)$/.exec(lines[i]);
    if (type && type[1].trim()) typeValue = typeValue || type[1].trim();
  }
  const next = enabled ? (typeValue || "guidance") : "disabled";
  if (statusIndex >= 0) lines[statusIndex] = `status: ${next}`;
  else lines.splice(1, 0, `status: ${next}`);
  return lines.join("\n");
}

async function toggleSkillEnabled(skill) {
  const id = skill?.id || skill?.name || "";
  const next = !skillEnabled(skill);
  const markdown = setSkillDocStatus(skill?.markdown || "", next);
  if (!id || !markdown) {
    showNotice("这个 Skill 没有可编辑的 SKILL.md 文档", "error");
    return;
  }
  try {
    const result = await post("/api/skills", { id, markdown });
    if (!result.ok) throw new Error(result.error || "切换失败");
    await loadSkills(true);
    renderSkills();
    showNotice(next ? `已启用「${normalizeSkillTitle(skill)}」` : `已停用「${normalizeSkillTitle(skill)}」`, "info");
  } catch (error) {
    showNotice(error.message || "切换 Skill 状态失败", "error");
  }
}


async function deleteSkill(skill) {
  const id = skill?.id || skill?.name || "";
  if (!id) return;
  const confirmed = await confirmDialog({
    title: `删除 Skill「${normalizeSkillTitle(skill)}」`,
    message: "会删除工作区里的 SKILL.md 文件，无法恢复。",
    confirmLabel: "删除",
    danger: true,
  });
  if (!confirmed) return;
  try {
    const result = await post("/api/skills", { action: "delete", id });
    if (!result.ok) throw new Error(result.error || "删除失败");
    await loadSkills(true);
    renderSkills();
    showNotice("Skill 已删除", "info");
  } catch (error) {
    showNotice(error.message || "删除 Skill 失败", "error");
  }
}

function renderVehicleFlightModesPanel() {
  const panel = els.vehicleFlightModesPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const modes = setupSnapshot().summary?.flight_modes || {};
  const rows = [["当前模式", modes.current_mode]];
  for (let i = 1; i <= 6; i += 1) rows.push([`飞行模式 ${i}`, modes[`flight_mode_${i}`]]);
  panel.innerHTML = `<section class="setup-detail-card wide">${setupRows(rows)}</section>${dataSourceRibbon(["HEARTBEAT", "COM_FLTMODE1-6"])}${readOnlyRibbon()}`;
}

function extractApiSuccess(data, fallback = "ok") {
  if (data?.ok === false || data?.result?.ok === false) {
    throw new Error(extractApiError(data, "command failed"));
  }
  const candidates = [
    data?.message,
    data?.result?.message,
    data?.result?.data?.message,
    data?.progress?.message,
  ];
  for (const item of candidates) {
    if (typeof item === "string" && item.trim()) return item;
  }
  return fallback;
}

