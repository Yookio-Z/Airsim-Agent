/* // 设置面板组：工具/记忆/技能列表、vehicle Setup/波形/参数、连接列表与详情 */

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
      "支持 OpenAI-compatible 与 Anthropic API。输入能力和上下文窗口默认按模型 ID 自动识别，也可手动覆盖。",
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

function compactJson(value, maxLength = 180) {
  let text = "";
  try {
    text = JSON.stringify(value);
  } catch (_) {
    text = String(value || "");
  }
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
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

function renderSkills() {
  normalizeAgentSettingsCopy();
  if (!els.skillList) return;
  const skills = Array.isArray(skillsCache) ? skillsCache : [];
  const active = skills.filter((s) => s.enabled !== false).length;
  const total = skills.length;
  els.skillCount.textContent = `${active} / ${total}`;

  if (!skills.length) {
    els.skillList.innerHTML = `<div class="empty">尚未添加领域 Skill。</div>`;
    return;
  }

  // Trae/Codex 风格：只展示名称与描述，由模型按 description 决定是否使用
  els.skillList.innerHTML = skills.map((s) => {
    const id = s.id || s.name || "";
    const title = normalizeSkillTitle(s);
    const desc = s.purpose || s.description || "";
    const enabled = s.enabled !== false;
    return `
      <article class="skill-item ${enabled ? "" : "disabled"}" data-skill-id="${escapeHtml(id)}" role="button" tabindex="0" title="编辑 ${escapeHtml(title)}">
        <header>
          <strong>${escapeHtml(title)}</strong>
          <span class="skill-status">${enabled ? "启用" : "已停用"}</span>
        </header>
        <p>${escapeHtml(desc || "定义 Agent 在特定无人机任务中的操作流程与安全边界。")}</p>
      </article>
    `;
  }).join("");
}

function normalizeSkillTitle(skill) {
  const display = String(skill.display_name || skill.id || skill.name || "");
  return display.startsWith("skill:") ? display.slice("skill:".length) : display;
}

function openNewSkillModal() {
  openSkillModal("", { create: true });
}

function openSkillModal(skillId, options = {}) {
  const skill = skillsCache.find((s) => (s.id || s.name) === skillId);
  const creating = Boolean(options.create);
  if ((!skill && !creating) || !els.skillModal || !els.skillForm) return;
  const title = skill ? normalizeSkillTitle(skill) : "";
  els.skillModalTitle.textContent = creating ? "新建 Skill" : "编辑 Skill";
  els.skillModalSubtitle.textContent = creating ? "创建可复用的领域操作规程（SKILL.md）" : skillId;
  if (els.skillModalClose) {
    els.skillModalClose.textContent = "×";
    els.skillModalClose.title = "关闭";
  }
  const initialMarkdown = creating
    ? defaultSkillMarkdown("", title)
    : String(skill?.markdown || "");
  // Trae/Codex 风格：直接编辑 SKILL.md，frontmatter 决定 name/description 等元数据
  els.skillForm.innerHTML = `
    <input type="hidden" id="skillEditId" value="${escapeHtml(skillId)}">
    <input type="hidden" id="skillEditCreating" value="${creating ? "1" : "0"}">
    <div class="form-row">
      <label for="skillEditSlug">标识</label>
      <input id="skillEditSlug" value="${escapeHtml(skillId.replace(/^skill:/, ""))}" placeholder="inspection_workflow" ${creating ? "" : "disabled"}>
    </div>
    <div class="form-row">
      <label for="skillEditMarkdown">SKILL.md（frontmatter 的 name/description 决定模型何时使用）</label>
      <textarea id="skillEditMarkdown" class="skill-markdown-editor" spellcheck="false" rows="16">${escapeHtml(initialMarkdown)}</textarea>
    </div>
    <div class="modal-actions">
      <button type="button" id="skillModalCancelInline" class="secondary">取消</button>
      <button type="submit" class="primary">${creating ? "创建" : "保存"}</button>
    </div>
  `;
  const cancel = document.getElementById("skillModalCancelInline");
  if (cancel) cancel.addEventListener("click", closeSkillModal, { once: true });
  els.skillModal.hidden = false;
}

function defaultSkillMarkdown(skillId, title) {
  const name = skillId.replace(/^skill:/, "") || title || "new_skill";
  return `---
name: ${name}
description: Describe when and how the Agent should use this skill.

---

# ${name}

## Purpose

Describe the workflow this skill handles.

## When to Use

Describe the situation in which the Agent should apply this skill.

## Operating Rules

- Read current vehicle state before issuing commands.
- Use only tools exposed by the active backend.
`;
}

function closeSkillModal() {
  if (els.skillModal) els.skillModal.hidden = true;
  if (els.skillForm) els.skillForm.reset();
}

async function submitSkillForm() {
  const idInput = document.getElementById("skillEditId");
  const creating = document.getElementById("skillEditCreating")?.value === "1";
  const slug = String(document.getElementById("skillEditSlug")?.value || "").trim().toLowerCase();
  const markdown = String(document.getElementById("skillEditMarkdown")?.value || "").trim();
  const skillId = creating ? `skill:${slug}` : String(idInput?.value || "").trim();
  if (!skillId || (creating && !slug)) {
    showNotice("请填写 Skill 标识", "error");
    return;
  }
  if (!markdown) {
    showNotice("SKILL.md 内容不能为空", "error");
    return;
  }
  try {
    const result = creating
      ? await post("/api/skills", { action: "create", id: slug, markdown })
      : await post("/api/skills", { id: skillId, markdown });
    if (!result.ok) throw new Error(result.error || "Save failed");
    await loadSkills(true);
    closeSkillModal();
    renderSkills();
    showNotice(creating ? "Skill 已创建并加载" : "Skill 已保存并重新加载", "success");
  } catch (error) {
    showNotice(error.message || "Skill 保存失败", "error");
  }
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

function valueText(value, fallback = "--") {
  return value === undefined || value === null || value === "" ? fallback : String(value);
}

function isRealVehicleRuntime(runtime = latestState?.tool_runtime || {}) {
  const capabilities = runtime.backend_profile?.capabilities || {};
  const drone = runtime.drone || {};
  return Boolean(capabilities.real_vehicle || drone.real_vehicle);
}

function hasReliableVehicleMapPosition(drone = {}, runtime = latestState?.tool_runtime || {}) {
  if (drone.map_position_valid === true) return true;
  if (isRealVehicleRuntime(runtime)) {
    if (drone.map_position_valid === false) return false;
    const gps = drone.gps || {};
    const fixType = Number(drone.gps_fix_type || 0);
    const accuracy = Number(drone.gps_horizontal_accuracy_m);
    const accuracyGood = !Number.isFinite(accuracy) || accuracy <= 50;
    return Boolean(gps.lat && gps.lon && Math.abs(Number(gps.lat)) > 0.001 && fixType >= 3 && accuracyGood);
  }
  return true;
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
    if (remoteTarget) rows.push(["PX4 目标", remoteTarget]);
  } else {
    rows.push(["实际端点", actualLinkSummary(link)]);
    if (remoteTarget) rows.push(["PX4 目标", remoteTarget]);
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

function setupSnapshot() {
  return vehicleSetupCache && typeof vehicleSetupCache === "object" ? vehicleSetupCache : {};
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

function formatNumber(value, digits = 1, unit = "") {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return `${number.toFixed(digits)}${unit}`;
}

function formatSignedNumber(value, digits = 1, unit = "") {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  const prefix = number > 0 ? "+" : "";
  return `${prefix}${number.toFixed(digits)}${unit}`;
}

function setupRows(rows) {
  return `
    <dl class="vehicle-info-grid">
      ${rows.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(valueText(value))}</dd>`).join("")}
    </dl>
  `;
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

function renderUnavailableSetup(panel, title = "未连接 PX4") {
  if (!panel) return true;
  if (setupConnected()) return false;
  panel.dataset.pidMounted = "";
  panel.dataset.sensorMounted = "";
  panel.dataset.waveformMounted = "";
  panel.innerHTML = `<div class="setup-empty"><strong>${escapeHtml(title)}</strong><span>连接真实 PX4 后这里会显示实时配置与遥测。</span></div>`;
  return true;
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

function drawMiniHistoryCanvas(canvasId, entries, fields) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(320, Math.floor(rect.width || 420));
  const height = Math.max(120, Math.floor(rect.height || 150));
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.floor(width * dpr) || canvas.height !== Math.floor(height * dpr)) {
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#101219";
  ctx.fillRect(0, 0, width, height);
  const plot = { x: 28, y: 14, w: width - 40, h: height - 26 };
  const latest = Number(entries.at(-1)?.sec || 0);
  const start = Math.max(0, latest - 8);
  const series = fields.map((field) => ({
    ...field,
    points: entries
      .filter((entry) => Number(entry.sec || 0) >= start)
      .map((entry) => ({ x: Number(entry.sec || 0) - latest, y: Number(entry[field.field]) }))
      .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y)),
  }));
  const values = series.flatMap((item) => item.points.map((point) => point.y));
  let [minY, maxY] = robustChartRange(values, [-1, 1]);
  const pad = Math.max(0.1, (maxY - minY) * 0.1);
  minY -= pad;
  maxY += pad;
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = plot.y + (plot.h * i) / 4;
    ctx.beginPath();
    ctx.moveTo(plot.x, y);
    ctx.lineTo(plot.x + plot.w, y);
    ctx.stroke();
  }
  const xFor = (x) => plot.x + ((x + 8) / 8) * plot.w;
  const yFor = (y) => plot.y + plot.h - ((y - minY) / (maxY - minY)) * plot.h;
  series.forEach((item) => {
    if (!item.points.length) return;
    ctx.strokeStyle = item.color;
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    item.points.forEach((point, index) => {
      const x = xFor(point.x);
      const y = yFor(point.y);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
  ctx.fillStyle = "rgba(210,218,235,0.62)";
  ctx.font = "11px Inter, Segoe UI, sans-serif";
  ctx.textAlign = "right";
  ctx.fillText(maxY.toFixed(1), plot.x - 5, plot.y + 4);
  ctx.fillText(minY.toFixed(1), plot.x - 5, plot.y + plot.h);
  ctx.textAlign = "left";
  fields.forEach((field, index) => {
    ctx.fillStyle = field.color;
    ctx.fillText(field.label, plot.x + 26 * index, 10);
  });
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

function renderVehicleFlightModesPanel() {
  const panel = els.vehicleFlightModesPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const modes = setupSnapshot().summary?.flight_modes || {};
  const rows = [["当前模式", modes.current_mode]];
  for (let i = 1; i <= 6; i += 1) rows.push([`飞行模式 ${i}`, modes[`flight_mode_${i}`]]);
  panel.innerHTML = `<section class="setup-detail-card wide">${setupRows(rows)}</section>${dataSourceRibbon(["HEARTBEAT", "COM_FLTMODE1-6"])}${readOnlyRibbon()}`;
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

function parameterValueFromCache(name) {
  const params = vehicleParametersCache?.parameters || [];
  const found = params.find((param) => param.name === name);
  if (found) return valueText(found.value_text ?? found.value);
  const setup = setupSnapshot();
  return valueText((setup.parameter_highlights || {})[name], "--");
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

function drawPidTuningChart() {
  const canvas = document.getElementById("vehiclePidCanvas");
  if (!canvas) return;
  const setup = setupSnapshot();
  const history = setup.history || {};
  const config = pidChartConfig(activePidTuningView);
  const rawPoints = Array.isArray(history[config.history]) ? history[config.history] : [];
  const lastSec = Number(rawPoints.at(-1)?.sec || 0);
  const windowSec = config.windowSec || 8;
  const startSec = Math.max(0, lastSec - windowSec);
  const points = rawPoints.filter((point) => Number(point.sec || 0) >= startSec);
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(640, Math.floor(rect.width || 800));
  const height = Math.max(320, Math.floor(rect.height || 420));
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
  ctx.fillStyle = "#f7f8fb";
  ctx.fillRect(0, 0, width, height);
  const plot = { x: 58, y: 34, w: width - 82, h: height - 86 };
  ctx.strokeStyle = "#dfe3eb";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 6; i += 1) {
    const x = plot.x + (plot.w * i) / 6;
    ctx.beginPath();
    ctx.moveTo(x, plot.y);
    ctx.lineTo(x, plot.y + plot.h);
    ctx.stroke();
  }
  for (let i = 0; i <= 6; i += 1) {
    const y = plot.y + (plot.h * i) / 6;
    ctx.beginPath();
    ctx.moveTo(plot.x, y);
    ctx.lineTo(plot.x + plot.w, y);
    ctx.stroke();
  }
  const response = decimateChartSeries(points.map((p) => ({ x: Number(p.sec || 0) - startSec, y: Number(p[config.response]) })).filter((p) => Number.isFinite(p.y)), plot.w * 1.5);
  const setpoint = config.setpoint ? decimateChartSeries(points.map((p) => ({ x: Number(p.sec || 0) - startSec, y: Number(p[config.setpoint]) })).filter((p) => Number.isFinite(p.y)), plot.w * 1.5) : [];
  const all = [...response, ...setpoint];
  const maxX = Math.max(3, windowSec, ...all.map((p) => p.x));
  const range = robustChartRange(all.map((p) => p.y), config.defaultRange || [-1, 1]);
  let minY = range[0];
  let maxY = range[1];
  const padY = Math.max(1, (maxY - minY) * 0.12);
  minY -= padY;
  maxY += padY;
  const xFor = (x) => plot.x + (x / maxX) * plot.w;
  const yFor = (y) => plot.y + plot.h - ((y - minY) / (maxY - minY)) * plot.h;
  const drawSeries = (series, color) => {
    if (!series.length) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    series.forEach((point, index) => {
      const x = xFor(point.x);
      const y = yFor(point.y);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  };
  ctx.save();
  ctx.beginPath();
  ctx.rect(plot.x, plot.y, plot.w, plot.h);
  ctx.clip();
  drawSeries(response, "#20a7e2");
  drawSeries(setpoint, "#8bc34a");
  ctx.restore();
  ctx.strokeStyle = "#aeb5c2";
  ctx.strokeRect(plot.x, plot.y, plot.w, plot.h);
  ctx.fillStyle = "#343944";
  ctx.font = "13px Inter, Segoe UI, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText(config.title, plot.x + plot.w / 2, 20);
  ctx.textAlign = "right";
  ctx.fillText(maxY.toFixed(1), plot.x - 8, plot.y + 5);
  ctx.fillText(minY.toFixed(1), plot.x - 8, plot.y + plot.h);
  ctx.save();
  ctx.translate(16, plot.y + plot.h / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = "center";
  ctx.fillText(config.unit, 0, 0);
  ctx.restore();
  ctx.textAlign = "center";
  ctx.fillText("sec", plot.x + plot.w / 2, height - 18);
  if (!response.length) {
    ctx.fillStyle = "#697184";
    ctx.fillText("等待实时 MAVLink 遥测...", plot.x + plot.w / 2, plot.y + plot.h / 2);
  }
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

const WAVEFORM_COLORS = ["#42d7ef", "#55dd83", "#ef6bb7", "#ff8a2a", "#9a5cf5", "#f4d35e", "#4ea1ff", "#ff5c7a", "#9fe870", "#d1d8e6"];

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

function timestampText(value) {
  const seconds = Number(value || 0);
  if (!Number.isFinite(seconds) || seconds <= 0) return "--";
  return new Date(seconds * 1000).toLocaleTimeString();
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
  els.connectionDetailStatus.classList.toggle("connected", connected);
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

function normalizeRosGatewayUrl(value) {
  const text = String(value || "").trim();
  if (!text) return "http://127.0.0.1:8766";
  if (/^https?:\/\//i.test(text)) return text;
  return `http://${text}`;
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
    const resp = await fetch("/api/settings/connections/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ connection_id: conn.id }),
    });
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

function connectionMatchesBackend(connection, backend) {
  const type = String(connection?.type || "").toLowerCase();
  const normalized = String(backend || "").toLowerCase();
  if (normalized === "airsim") return type === "airsim";
  if (normalized === "px4_ros2") return ["px4_ros2", "ros2", "ros", "px4_ros"].includes(type);
  if (normalized === "px4_mavlink") return ["auto", "udp", "tcp", "serial", "mavlink", "px4"].includes(type);
  return false;
}

function renderSystemConnection(drone = {}, toolRuntime = {}) {
  const connected = Boolean(toolRuntime.connected) && !toolRuntime.stale_connection;
  let activeChanged = false;

  if (connected && connectionsCache.length) {
    const backend = toolRuntime.backend || toolRuntime.backend_profile?.id || "airsim";
    const current = connectionsCache.find((connection) => connection.id === activeConnectionId);
    const expected = current && connectionMatchesBackend(current, backend)
      ? current
      : connectionsCache.find((connection) => connectionMatchesBackend(connection, backend));
    if (expected && expected.id !== activeConnectionId) {
      activeConnectionId = expected.id;
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
  renderActiveVehicleSetupPanel("runtime");
  if (activeSystemSettingsSection === "parameters") {
    renderVehicleParametersPanel();
  }
}

