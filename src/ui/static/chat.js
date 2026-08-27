/* // 聊天渲染：两级折叠块(buildAgentTurn/updateAgentTurn)、renderChat、平滑流式(smoothStream)、SSE */

const turnNodes = new Map();

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
  procFold.appendChild(procSummary);
  procFold.appendChild(toolLines);
  // 思考块作为时间线的一员放进工具行容器：位置由 updateAgentTurn 决定——
  // 默认最前，出现"理解任务"等阶段行后紧跟其后（思考发生在理解/规划期间）
  toolLines.appendChild(thinkFold2);

  const answerBody = document.createElement("div");
  answerBody.className = "answer-body";

  const entry = {
    root,
    kind: "agent",
    errorPill,
    procFold,
    procState,
    procLatest,
    thinkFold2,
    thinkState,
    thinkLatest,
    thinkFull,
    toolLines,
    answerBody,
    renderedTrace: 0,
    rowNodes: [],
    rowSigs: [],
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

function toolLineNode(item) {
  const row = document.createElement("div");
  row.className = `tool-line ${item.status || "completed"} kind-${item.kind || "tool"}`;
  const badge = document.createElement("em");
  badge.className = "tool-badge";
  badge.textContent = processKindLabel(item.kind || "tool");
  const title = document.createElement("strong");
  title.textContent = item.tool ? humanToolLabel(item.tool, item.title) : humanThoughtTitle(item.title || "");
  const body = document.createElement("span");
  body.className = "tool-line-body";
  const bodyText = humanThoughtBody(item.body || "", item.tool || "");
  row.appendChild(badge);
  row.appendChild(title);
  if (bodyText) {
    const isLong = bodyText.length > 140;
    if (isLong) {
      // 长正文可展开：默认隐藏，点击展开看完整内容
      body.textContent = bodyText.slice(0, 140);
      body.className = "tool-line-body foldable";
      body.title = "点击展开完整内容";
      body.addEventListener("click", () => {
        body.textContent = bodyText;
        body.title = "";
        body.classList.remove("foldable");
      });
    } else {
      body.textContent = bodyText;
    }
    row.appendChild(body);
  }
  return row;
}

function rowSignature(item) {
  return `${item.status || ""}|${item.title || ""}|${item.body || ""}`;
}

// 思考块在时间线中的位置：紧跟第一个阶段行（理解任务等非工具/校验行）
// 之后——LLM 思考发生在任务理解与规划期间；没有阶段行时保持在最前
function placeThinkFold(entry, reasoning) {
  if (!reasoning) return;
  const rows = entry.rowNodes.filter(Boolean);
  let phaseIdx = -1;
  for (let i = 0; i < rows.length; i++) {
    const cls = rows[i].classList;
    if (!cls.contains("kind-tool") && !cls.contains("kind-verify")) {
      phaseIdx = i;
      break;
    }
  }
  if (phaseIdx === -1) {
    if (entry.toolLines.firstElementChild !== entry.thinkFold2) {
      entry.toolLines.insertBefore(entry.thinkFold2, entry.toolLines.firstChild);
    }
    return;
  }
  const anchor = rows[phaseIdx].nextElementSibling;
  if (anchor !== entry.thinkFold2) {
    entry.toolLines.insertBefore(entry.thinkFold2, anchor);
  }
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
  // 声明必须先于下方 scroll 块使用（TDZ：const 在函数内声明前引用会抛
  // ReferenceError，pending/running 消息首轮渲染必然触发，导致发送提交
  // 在 fetch 前整体崩溃——用户消息上屏后无任何响应）
  const hasProcess = Boolean(reasoning) || entry.renderedTrace > 0;

  // 错误徽标
  entry.errorPill.style.display = isError ? "" : "none";

  // 工具/校验步骤：先增量追加（跳过推理类条目——已在思考块里），
  // 再判定过程折叠块可见性——顺序不能反，否则首轮判定时步骤数为 0
  // 会把折叠块误隐藏，之后又被快速路径跳过永不恢复
  const linkedRun = run && message.run_id && run.run_id === message.run_id ? run : null;
  const processTrace = Array.isArray(linkedRun?.process_trace) && linkedRun.process_trace.length
    ? linkedRun.process_trace
    : (Array.isArray(details.process_trace) ? details.process_trace : []);
  const visible = processTrace.filter((item) => {
    const kind = String(item.kind || "");
    const title = String(item.title || "");
    if (kind === "reasoning") {
      // 阶段行（理解任务/选择工具/技能参考）保留在时间线里，展示 Agent
      // 每个阶段在做什么；模型思考/状态总结的内容分别在思考块和正文里，
      // 重复成行只会增加噪音
      return !/模型思考|模型推理|状态总结/.test(title);
    }
    if (item.tool === "memory_store") return false;
    const t = humanThoughtTitle(title);
    return Boolean(t) && !/模型思考|模型推理/.test(title);
  });
  // 增量追加新行；已渲染的行若内容变化（running 行完成后合并为
  // "动作 → 结果"）原位替换，避免残留 "2/4 · 正在执行" 这类中间文案
  while (entry.renderedTrace < visible.length) {
    const node = toolLineNode(visible[entry.renderedTrace]);
    entry.toolLines.appendChild(node);
    entry.rowNodes.push(node);
    entry.rowSigs.push(rowSignature(visible[entry.renderedTrace]));
    entry.renderedTrace += 1;
  }
  for (let i = 0; i < Math.min(entry.rowNodes.length, visible.length); i++) {
    const sig = rowSignature(visible[i]);
    if (entry.rowSigs[i] !== sig) {
      const node = toolLineNode(visible[i]);
      entry.toolLines.replaceChild(node, entry.rowNodes[i]);
      entry.rowNodes[i] = node;
      entry.rowSigs[i] = sig;
    }
  }
  placeThinkFold(entry, reasoning);

  // 运行中：滚动到最新内容，让用户始终看到最新的思考/工具输出
  if (running && hasProcess && entry.renderedTrace > 0) {
    const thoughtBody = entry.procFold.querySelector(".thought-body");
    if (thoughtBody) {
      thoughtBody.scrollTop = thoughtBody.scrollHeight;
    }
    if (entry.procFold.open) {
      entry.procFold.scrollTop = entry.procFold.scrollHeight;
    }
  }

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
      // 完成后保持展开：用户希望回顾每一步 Agent 如何思考、如何决定
      if (entry.procFold.open && !entry.userToggled) {
        entry.procFold.open = true;
      }
    }

    // 内层思考折叠块：推理全文；运行中"伪流式"按段平滑释放——模型往往整块
    // 一次性吐出推理（思考期间不流 token），前端逐段显示避免"思考结束才
    // 出现"的突兀感；token 真正流式到达时平滑会自动追平。
    if (reasoning) {
      entry.thinkFold2.style.display = "";
      const thinkId = `think_${message.id || message.run_id || entry.root.dataset.messageId}`;
      if (reasoning !== entry.lastReasoning) {
        entry.lastReasoning = reasoning;
        smoothStream.targets.set(thinkId, reasoning);
        if (!smoothStream.shown.has(thinkId)) smoothStream.shown.set(thinkId, 0);
        smoothStartLoop();
      }
      let revealed = "";
      if (running) {
        const target = smoothStream.targets.get(thinkId) || "";
        const shown = smoothStream.shown.get(thinkId) || 0;
        revealed = target.slice(0, shown);
      } else {
        // 完成/失败时直接展示全文并清掉平滑状态
        revealed = reasoning;
        smoothStream.targets.delete(thinkId);
        smoothStream.shown.delete(thinkId);
      }
      if (revealed !== entry.lastRevealedThink) {
        entry.lastRevealedThink = revealed;
        entry.thinkFull.textContent = revealed || "思考中…";
      }
      entry.thinkLatest.textContent = running ? latestThinkLine(revealed || reasoning) : firstThinkLine(reasoning);
      entry.thinkLatest.scrollLeft = running ? entry.thinkLatest.scrollWidth : 0;
      if (running && !entry.thinkFold2.open && !entry.thinkUserToggled) {
        entry._programmaticThink = true;
        entry.thinkFold2.open = true;
      }
      if (!running && entry.thinkFold2.open && !entry.thinkUserToggled) {
        entry._programmaticThink = true;
        entry.thinkFold2.open = false; // 完成后思考也收起（外层已折叠全部过程）
      }
    } else {
      entry.thinkFold2.style.display = "none";
    }
  } else {
    entry.procFold.style.display = "none";
    entry.thinkFold2.style.display = "none";
  }

  // 内层思考块的 toggle 归属（用户手动 vs 程序设置）
  if (!entry._thinkToggleWired && entry.thinkFold2) {
    entry._thinkToggleWired = true;
    entry.thinkFold2.addEventListener("toggle", () => {
      if (entry._programmaticThink) {
        entry._programmaticThink = false;
        return;
      }
      entry.thinkUserToggled = true;
    });
  }

  // 正文：平滑分批释放（smoothShownContent）；运行中推理不占正文，
  // 完成后填入 LLM 总结
  const text = smoothShownContent(message).trim();
  if (text !== entry.lastAnswer) {
    entry.lastAnswer = text;
    entry.answerBody.innerHTML = text ? renderMarkdown(text) : "";
    entry.answerBody.style.display = text ? "" : "none";
  }

  entry.root.classList.toggle("error", isError);
  entry.lastStatus = message.status;
}

function renderChat(messages, run, llm) {
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
    if (entry.kind === "agent") updateAgentTurn(entry, message, run, llm);
    else updateUserTurn(entry, message);
  }
  for (const [id, entry] of [...turnNodes]) {
    if (!liveIds.has(id)) {
      entry.root.remove();
      turnNodes.delete(id);
    }
  }

  const scrollTargetId = pendingScrollTargetId;
  const shouldScroll = !scrollTargetId && (forceNextChatScroll || shouldStickToChatBottom());
  if (scrollTargetId) scrollMessageIntoView(scrollTargetId);
  else if (shouldScroll) scrollChatToEnd();
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
  const showThinkingPill = active && mode === "chat" && !thoughts;
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

function renderMessageAttachments(attachments) {
  if (!Array.isArray(attachments) || !attachments.length) return "";
  const images = attachments.map((item) => {
    const src = String(item.url || item.data_url || "");
    if (!(src.startsWith("/api/attachments/") || src.startsWith("data:image/"))) return "";
    return `<img src="${escapeHtml(src)}" alt="${escapeHtml(item.name || "attached image")}" loading="lazy">`;
  }).filter(Boolean).join("");
  return images ? `<div class="message-images">${images}</div>` : "";
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
  if (["reasoning", "tool", "verify", "memory", "system"].includes(explicit)) return explicit;
  const title = String(item?.title || "").toLowerCase();
  if (item?.tool) return "tool";
  if (/校验|verify|回读/.test(title)) return "verify";
  return "reasoning";
}

function processKindLabel(kind) {
  if (kind === "tool") return "工具";
  if (kind === "verify") return "校验";
  if (kind === "memory") return "记忆";
  if (kind === "system") return "系统";
  return "模型";
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

function renderLoopTrace(loopState) {
  const decisions = Array.isArray(loopState?.decisions) ? loopState.decisions : [];
  const results = Array.isArray(loopState?.results) ? loopState.results : [];
  if (!decisions.length && !results.length) return "";
  const rows = decisions.map((decision, index) => {
    if (!decision.action || decision.action === "memory_store") return "";
    const result = results.find((item) => Number(item.step_index || 0) === index + 1);
    const action = decision.action || "complete";
    const state = decision.is_complete ? "completed" : result ? (result.ok ? "completed" : "failed") : "planned";
    const subTools = renderSkillSubTools(result);
    const reason = humanDecisionReason(decision.reason || result?.data?.message || "", action);
    return `
      <article class="loop-row ${state}">
        <div class="loop-head">
          <span class="step-state ${state}">${index + 1}</span>
          <strong>${escapeHtml(humanToolLabel(action))}</strong>
          <small>${escapeHtml(reason)}</small>
        </div>
        ${decision.reflection ? `<p>${escapeHtml(decision.reflection)}</p>` : ""}
        ${subTools}
      </article>
    `;
  }).filter(Boolean).join("");
  if (!rows) return "";
  const status = loopState.status ? ` · ${loopState.status}` : "";
  return `
    <div class="detail-note loop-note">
      <strong>执行过程${escapeHtml(status)}</strong>
      <div class="loop-trace">${rows}</div>
    </div>
  `;
}

function humanDecisionReason(reason, action = "") {
  const text = String(reason || "").trim();
  const normalized = text.toLowerCase();
  if (action === "airsim_take_photo" || normalized.includes("capture the current camera frame")) {
    return "获取当前摄像头画面";
  }
  if (action === "airsim_vlm_analyze_image" || normalized.includes("analyze the captured camera frame")) {
    return "调用所选多模态模型分析画面";
  }
  if (action === "airsim_vlm_confirm_target" || normalized.includes("confirm the requested target")) {
    return "确认画面中是否存在目标";
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

function openModelModal(modelId) {
  closeAllDropdowns();
  const model = modelId ? modelsCache.find((m) => m.id === modelId) : null;
  els.modelEditId.value = model ? model.id : "";
  els.modelModalTitle.textContent = model ? "编辑模型" : "添加模型";
  els.modelName.value = model ? model.name || "" : "";
  els.modelProvider.value = model ? model.provider || "" : "";
  els.modelModelId.value = model ? model.model || "" : "";
  els.modelApiType.value = model ? model.api_type || "openai" : "openai";
  els.modelBaseUrl.value = model ? model.base_url || "" : "";
  els.modelApiKey.value = "";
  els.modelApiKey.type = "password";
  els.modelApiKey.placeholder = model?.key_hint
    ? `已保存 ${model.key_hint}，留空保持不变`
    : "输入 API Key";
  if (els.modelReasoningEffort) els.modelReasoningEffort.value = model?.reasoning_effort || "";
  if (els.modelThinkingMode) els.modelThinkingMode.value = model?.thinking_mode || "";
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

async function submitModelForm() {
  const isEdit = Boolean(els.modelEditId.value.trim());
  const payload = {
    id: els.modelEditId.value.trim(),
    name: els.modelName.value.trim(),
    provider: els.modelProvider.value.trim(),
    model: els.modelModelId.value.trim(),
    api_type: els.modelApiType.value,
    base_url: els.modelBaseUrl.value.trim(),
  };
  if (els.modelReasoningEffort) payload.reasoning_effort = els.modelReasoningEffort.value || "";
  if (els.modelThinkingMode) payload.thinking_mode = els.modelThinkingMode.value || "";
  const apiKey = els.modelApiKey.value.trim();
  if (apiKey || !isEdit) {
    payload.api_key = apiKey;
  }
  if (!payload.name || !payload.provider || !payload.model) {
    showNotice("请填写模型名称、Provider 和模型 ID", "error");
    return;
  }
  try {
    await saveModelToBackend(payload);
    await fetchModels();
    closeModelModal();
    if (els.agentSettingsDrawer && !els.agentSettingsDrawer.hidden) {
      renderModelConfig();
    }
    showNotice(isEdit ? "模型已更新" : "模型已添加", "success");
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
      <div class="config-actions">
        <button class="edit-model" data-action="edit" data-model-id="${escapeHtml(m.id)}">编辑</button>
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

function scrollChatToEnd() {
  requestAnimationFrame(() => {
    els.chatThread.scrollTop = els.chatThread.scrollHeight;
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

// ---------------------------------------------------------------------------
// Smooth streaming（借鉴 dsh-plugin-smooth-stream 的分批呈现算法）
//
// LLM 的 delta 到达速度远快于人阅读速度。这里不再逐 delta 全量重绘，而是：
//   1. delta 只更新目标内容（targets），渲染循环每 160ms 释放一批；
//   2. 释放点选在段落/行边界，且绝不切在未闭合代码块或表格中间
//      （extendToSafeMarkdown），Markdown 永远不会渲染到一半；
//   3. 新释放的正文带淡入动画；流式期间匀速跟随滚动，用户上滚即交还控制。
// ---------------------------------------------------------------------------

const smoothStream = {
  targets: new Map(),
  shown: new Map(),
  timer: null,
};

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
  if (inFence) return fenceFrom > 0 ? fenceFrom : pos;
  if (inTable) return tableFrom > 0 ? tableFrom : pos;
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

function smoothFlushMessage(id) {
  smoothStream.targets.delete(id);
  smoothStream.shown.delete(id);
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
        if (shouldStickToChatBottom() && els.chatThread) {
          els.chatThread.scrollTop = els.chatThread.scrollHeight;
        }
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
    render(latestState);
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

