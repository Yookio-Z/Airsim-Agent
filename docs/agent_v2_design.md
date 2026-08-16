# Agent V2 升级设计文档（超越三方 Agent 的无人机领域实现）

> 状态：设计评审稿。目标是把 `src/agent/` 从"能用"升级为"可审计、可诊断、可长跑、可拆解"的
> 无人机任务理解与执行 Agent，吸收 agenticros / deepseek-harness / openclaw-swarm 的可借鉴设计，
> 同时守住本系统的安全护城河（审批门、急停、安全校验、失败悬停）。

## 1. 背景与差距总结

对比三个第三方项目后，我们确认自身的四大短板与两个领域优势：

| 维度 | 差距 | 借鉴来源 |
|---|---|---|
| LLM 交互协议 | JSON schema 写在 prompt 里靠正则解析，无原生 function calling、无输出结构校验、无 token 计量 | dsh 的 ToolSchema + output.schema 校验；agenticros 的 FunctionDeclaration |
| 上下文管理 | 每步只带最近 5 条决策，无预算、无裁剪优先级、长任务早期观察丢失 | dsh 的 token-meter + compaction + spill |
| 可观测性 | 决策轨迹不落盘，失败后无法重建"模型看到了什么" | dsh 的事件溯源 Session（"model-visible means logged"） |
| 记忆 | 只被动写入聚合统计，LLM 无主动检索能力，无任务转录 | agenticros 的 memory_* 工具 + mission transcript sink |
| 领域规则 | 关键词意图识别在 planner / agent_loop / llm fallback 三处重复实现 | agenticros 的单一 planner 契约 + unmatched 反馈 |
| 任务拆解 | 单循环串行，无子任务机制 | dsh 的 subagent；openclaw-swarm 的"LLM 高层意图 + 确定性执行" |

两个领域优势（必须保留，不做削弱性重构）：
1. **安全体系**：审批门 + 急停 + SafetyArbiter + 失败悬停，三者皆无。
2. **双轨规划**：LLM 规划 + 规则回退 + LLM 不可用不发飞控指令。

## 2. 设计原则

1. **向后兼容优先**：所有新能力有降级路径（原生工具调用失败 → JSON 模式；token 预算不可知 → 现状裁剪）。
2. **安全关键路径不动**：工具执行顺序、审批、急停语义、`validate()` 飞行参数校验不做任何削弱。
3. **一次性取舍声明**：不做"多工具并行执行"。理由：(a) ToolRuntime 全局锁串行是安全设计；(b) 飞控工具必须互斥；(c) 并行收益主要是省往返，通过"一次 LLM 回复返回多个 tool_call、批量顺序执行"获得同等收益且零风险。
4. **新增模块独立、可单测**：协议校验、token 计量、run log、sub-agent 都是纯逻辑模块，不依赖后端连接。
5. **事件即事实**：run 的每个决策/结果/状态变化写入 append-only JSONL，UI 与诊断共用。

## 3. 架构总览

```text
AgentRuntime (submit_command / plan_execute / approval)
 ├─ LLMMissionPlanner ── LLMClient 层（新增 chat_tools 原生工具调用，保留 chat_json 回退）
 │     ├─ TokenMeter / ContextBudget（新增，决定每轮 prompt 内容与裁剪优先级）
 │     └─ validate_json_schema（新增，校验 LLM 输出与工具输出）
 ├─ MissionPlanner（规则回退，意图识别收敛到 command_slots.extract_intents）
 ├─ AgentLoop（新增：批量动作执行 / run logger / 失败分类）
 ├─ SubAgentRunner（新增：agent_subtask 工具，嵌套受控循环）
 ├─ ToolRuntime（新增：error_code / output_schema 校验 / 安全工具重试）
 ├─ AgentMemory（新增：facts 主动写入 / recall 主动检索 / run transcript）
 └─ RunLog（新增：JSONL 事件日志 + 重放）
```

## 4. 分模块设计

### 4.1 LLM 协议层（llm.py 扩展，新增 `llm_protocol.py` 放纯函数）

- **原生工具调用** `chat_tools(messages, tools, tool_choice="auto")`：
  - OpenAI 兼容端点：`tools=[{"type":"function","function":{name,description,parameters}}]`，解析 `message.tool_calls[]`（id/name/arguments JSON）。
  - Anthropic 端点：`tools=[{name,description,input_schema}]`，解析 content 块 `tool_use`。
  - 返回 `(tool_calls, text, usage, native)`；HTTP 400 或解析失败 → 自动回退 chat_json（现有 JSON 模式），`native=False`。
  - 错误分类：`_retryable_llm_error` 保留；新增 `_unsupported_tools_error`（400 + "tools"/"function" 关键字）判定降级而非重试。
- **严格输出校验** `validate_json_schema(value, schema)`：
  - 支持 type/required/properties/enum/items/minimum/maximum/nullable，~120 行纯函数。
  - 用途：VLM confirm/analyze 输出（normalize 前）、loop decision 输出、plan 输出。校验失败时：能补默认值就补（现有 normalize 已做），否则记 `validation_errors` 字段并继续（不抛异常，避免把 LLM 格式问题变成任务失败）。
- **TokenMeter**：
  - `estimate(text)`：CJK 按 1 token/字符，ASCII 按 len/4（粗略但稳定）。
  - `recalibrate(usage)`：用真实 usage.prompt_tokens 平滑修正系数（EMA，α=0.3）。
  - `estimate_messages(messages)`。
- **ContextBudget**：
  - `budget_for(config)` = `context_window * 0.7 − 输出预留(max_tokens 或 2048)`。
  - `fit(sections: list[(key, value, priority)], budget) -> dict`：按优先级从高到低保留，低优先级段先裁剪（截断/摘要行替换），保证总估计 ≤ budget。优先级：图片/指令(高) > 最近 3 步决策与结果(中) > tool_cards(中) > 观察/遥测(低) > memory/guidance(低)。
  - 接入 `decide_next_step` 与 `plan`：现有 `_compact_*` 字段级裁剪保留，新增总量级预算兜底。

### 4.2 事件溯源 RunLog（新模块 `run_log.py`）

- `RunLog(run_id)`：JSONL append-only 写 `.airsim_agent/runs/<run_id>.jsonl`，原子写（tmp+rename）。
- 事件：`run.start / command / plan / observation / decision / tool.call / tool.result / state / message / run.end`，每行 `{seq, ts, type, payload}`。
- 脱敏：LLM 原始请求/响应全文不入日志；只记 `{model, provider, prompt_tokens, completion_tokens, native}`；工具参数与结果全文入日志（无密钥）。
- `RunLogReader(run_id)`：重放为 `{run_id, command, decisions[], results[], summary, status}`；`RunLogStore.list(limit)`。
- 集成：`AgentLoop.run()` 接受可选 `logger` 回调；`AgentRuntime._plan_and_execute` 创建 RunLog 传入 loop 并记录 plan/命令/结束。`TaskRunStore` 继续负责任务状态；RunLog 负责轨迹事实。

### 4.3 批量动作执行（agent_loop.py + loop_types.py）

- `LoopDecision` 新增 `parallel_actions: list[dict] = []`（dict 而非嵌套 dataclass，避免序列化递归）。
- LLM 决策解析：`_decision_from_payload` 支持 payload 顶层 `actions` 数组（原生 tool_calls 展开为多个 decision；JSON 模式支持 `actions` 字段）。
- 执行：主 action + parallel_actions 按声明顺序**串行**执行（见设计原则 3），每个 action 独立产生 LoopActionResult；其中任一失败按现有失败计数规则处理。守卫（`_guard_decision`）只作用于主 action；parallel_actions 逐个过 `_sanitize_decision` 工具白名单。
- 收益：一次 LLM 回复执行 N 个工具，省 N−1 次往返；"起飞→悬停"等确定性序列可一批下发。

### 4.4 失败分类与恢复（agent_loop.py）

- 失败计数升级：`failure_count` 按"连续失败"计（同一步成功后清零），且区分：
  - `recoverable`（连接类/瞬时，`_is_connection_error` 命中或 error_code 为 TIMEOUT/RETRYABLE）→ 允许重试同工具（最多 2 次，退避 0.5s/1s）；
  - `terminal`（BLOCKED/安全拦截/INVALID_TOOL_OUTPUT）→ 直接按失败计数处理，不重试。
- 失败达到 3 次（保持现状阈值）整体 failed；超过 1 次 recoverable 重试仍失败则计入 failure_count。

### 4.5 工具层增强（tool_executor.py + loop_types.py）

- `ToolCallResult` 新增 `error_code: str = ""`；`to_dict()` 输出。
- `ToolSpec` 新增 `retries: int = 0`、`retry_delay_ms: int = 500`、`output_schema: dict | None = None`（is_concurrency_safe 预留但本版不启用并行，字段先不加，避免误导）。
- `execute()`：
  - 错误码归一化：`data.status` → error_code（blocked→`BLOCKED`、error→`TOOL_ERROR`、timeout→`TIMEOUT`、连接类→`CONNECTION`、超参→`INVALID_PARAMS`）。
  - 安全重试：仅对 READ_ONLY_TOOLS ∪ {显式 retries>0 的工具} 且 error_code ∈ {CONNECTION, TIMEOUT} 时重试（幂等性保证）。
  - 输出校验：有 `output_schema` 时校验 data，失败 → `ok=False, error_code="INVALID_TOOL_OUTPUT"`（保留原始 data 供诊断）。
- 为 VLM 两个工具与 `drone_get_status` 等核心读工具添加 output_schema。

### 4.6 规则收敛（command_slots.py + planner.py + agent_loop.py + llm.py）

- `command_slots.py` 新增共享常量与 `extract_intents(text) -> dict[str, bool]`：
  - 意图键：`visual / motion / search / track / photo / patrol / takeoff / land / return_home / hover / status / connect / visual_approach / open_image_analysis / target_confirmation`。
  - 关键词集合 = 现有三处实现（planner.wants_*、agent_loop._is_visual_request 系列、llm._fallback_loop_decision/_filter_unsolicited_steps/_command_target_class）的**并集**，行为只增不减。
  - `extract_target_class(text)` 合并 planner.TARGET_ALIASES 与 llm._command_target_class 两套别名。
- 三处调用点全部切换为共享函数；`extract_command_slots` 保持不变（参数抽取）。
- 回归保护：`tests/test_plan_execute_react.py` 等覆盖主要意图路径；新增 `test_command_slots_intents.py` 固化中英文意图映射。

### 4.7 记忆主动化（memory.py + runtime.py）

- `AgentMemory` 新增：
  - `remember_fact(key, value, tags)` → `facts` 字典（LLM 主动写入，bounded）。
  - `remember_transcript(run_id, command, tool, ok, summary)` → `runs` 列表（bounded 50，供复盘）。
  - `recall(query, limit=5)` → 对 facts/missions/lessons/runs 做词重叠打分 + 新近度加成（复用 guidance 的启发式）。
- runtime `_execute_agent_tool` 处理 `memory_recall` / `memory_remember`（与 memory_store 同级）；
  - 从 `_internal_tools()` 移除这两个名字（允许 loop 调用）；在 runtime 构建 tool_cards 时注入这两张卡片（`_planner_tool_cards` / loop 的 `_decision_cards` 上游）。
- 记忆内容进 prompt：`_compact_memory` 增加 facts（最多 8 条）与最近 runs（3 条摘要）。

### 4.8 子 Agent（新模块 `sub_agent.py`）

- `SubAgentRunner.run(goal, constraints, tool_cards, capabilities, model_id, max_steps=6) -> dict`：
  - 复用 `AgentLoop`（独立 LoopState、独立 RunLog 子日志 `run:<parent>.<sub>`），同一 ToolRuntime、同一 `execute_tool`（runtime._execute_agent_tool，父线程内同步执行 → caller_owns_run 成立，审批/守卫自然覆盖）。
  - 系统提示语：子 agent 聚焦单一目标、只调用列表内工具、禁止飞控高频控制（与主 loop 相同约束）、结束时给结构化报告。
  - 报告：`{status, summary, steps: [{tool, ok, data_summary}], findings}`。
- 注册为工具 `agent_subtask`，参数 `{goal, constraints, max_steps, model_id}`；仅当主 loop 有权限时暴露。
- 使用场景：视觉搜索子任务（把 skill:search 的多步循环交给子 agent）、多目标确认、长序列拆解。父 loop 把子 agent 报告作为一步观察，不展开子步骤。

### 4.9 可观测性收尾

- RunLog 提供轨迹事实；`AgentRuntime` 增加 `run_trace(run_id)` API 供 UI/诊断（读取 RunLogReader）。
- 现有事件流/thought trace/replay 全部保留。

## 5. 实施顺序与验证

| 阶段 | 内容 | 验证 |
|---|---|---|
| A | llm_protocol.py（validate_json_schema / TokenMeter / ContextBudget / chat_tools） | 新增单测 + 全量回归 |
| B | run_log.py + AgentLoop logger 接入 | 单测 + 回归 |
| C | 上下文预算接入 decide_next_step/plan | 单测 + 回归 |
| D | 工具层 error_code / output_schema / 安全重试 | 单测 + 回归 |
| E | 规则收敛到 extract_intents | test_plan_execute_react 全绿 + 新意图单测 |
| F | 记忆主动化 + sub_agent | 单测 + 回归 |
| G | 批量动作执行（loop 多 action） | 单测 + 回归 |
| H | 全量回归 + 评判 Agent 终审 | pytest 全绿 |

每个阶段结束跑 `uv run pytest tests/ -x -q`，保证不破坏现有行为。

## 6. 风险与取舍记录

1. **不做并行工具执行**（原则 3）：安全关键域 + 全局锁 + 批量顺序执行已获主要收益。
2. **VLM/拍照工具保持串行**：它们读写共享的 `_last_visual_frame`，并行有竞态。
3. **compaction 不做 LLM 摘要**（dsh 做法）：飞行任务 loop 步数有限（≤10），决策 reason/reflection 已是模型生成的摘要；用"保留最近 N 步 + 早前步骤摘要行"的确定性压缩，零额外 LLM 成本。
4. **sub-agent 与父 loop 同线程同步执行**：避免执行槽/审批上下文分裂；代价是子任务期间父 loop 阻塞（可接受，因为父 loop 本来就串行等待工具结果）。
5. **原生工具调用降级路径**：provider 不支持 tools 参数时回退 JSON 模式，行为与现在完全一致。

## 7. 评审修订记录（2026-08-16 评审 Agent 意见采纳）

### 7.1 必须修改项（全部采纳）

1. **新增任务契约与完成判据验证**（领域超越点，原设计遗漏的最重要短板）：
   - `MissionPlan.goal = {objective, target, success_criteria: [{metric, params, tolerance}]}`；criteria 词汇表（机器可判定）：
     `target_confirmed`（VLM confirm target_found）、`position_reached(x,y,z,tolerance)`、`flying_at(altitude,tolerance)`、
     `landed`、`photo_taken`、`status_ok`（无失败工具）、`mission_progress_complete`。
   - LLM plan 路径：LLM 输出 goal（criteria 从固定词汇表选择）；规则路径：按 intent 合成（search→target_confirmed 可选、fly_to→position_reached、land→landed、takeoff→flying_at、patrol→mission_progress_complete 或 flight 完成；默认 []）。
   - loop 侧：`decision.is_complete` 时先跑 `verify_success(goal, state, telemetry)`；不满足 → 允许**一次**纠正动作（按 criteria 类型映射：position_reached→drone_get_status 重读、target_confirmed→airsim_vlm_confirm_target 重确认、landed→drone_get_status），再验证；仍不满足 → 完成但 summary/memory 标记 `verification_status="failed"`（与 plan 路径 `_verify_run_outcome` 语义对齐）。criteria 无法评估（无遥测）→ 接受完成并记录 warning。
2. **工具 JSON Schema 来源**：`tool_schema_from_spec(name, spec, card)` 合成器——ToolSpec.parameters（annotation→type、无 default→required）+ ToolCard.inputs（description）+ 飞行工具手工约束表（altitude 0.5-120、velocity 0.2-20、x/y/z 范围、target_class enum）。集中维护在 `llm_protocol.py`。
3. **原生工具调用降级判定**：ModelRegistry 每模型显式 `native_tools` 能力标志（并入 `infer_model_capabilities`，可被用户配置覆盖）为主闸门；错误解析 `error.param == "tools"` 优先、消息关键字兜底；降级原因写入 RunLog 并区分"provider 不支持"（降级）与"schema 无效"（抛错）。
4. **native 模式完成/过程文本协议**：响应含 tool_calls → 非完成，展开执行；无 tool_calls → `is_complete=True`，assistant 文本作为 reason（UI 过程文本依赖它）。
5. **Anthropic 协议**：`_build_body` 支持 tools/tool_choice；解析 tool_use 块（与文本混排）；`stop_reason=="tool_use"` 处理；assistant 消息保留 tool_use 块。
6. **批量模式守卫覆盖**：parallel_actions 中每个动作包装成单 action decision 逐个过 `_guard_decision`；含飞行工具的批量仅允许主 action（并行 actions 仅限 READ_ONLY 类）。
7. **批量审批语义**：批量内多个 high-risk 工具合并为**一次**审批请求（列出全部 N 个工具）；任一被拒 → 整批中止。
8. **target_class 统一优先级**：truck > bus > person > car > drone，`extract_target_class` 唯一实现，全路径一致。
9. **RunLog 图片脱敏与保留策略**：`image_base64` 字段只记 SHA256+尺寸+路径；`runs/` 保留最近 200 个 run；事件带 `version` 字段。
10. **子 agent 提示注入**：`decide_next_step` 增加 `system_prompt` / `fallback_enabled` override 参数；子 agent tool_cards 排除 `agent_subtask`，嵌套深度上限 1；子 agent 内 LLM 不可用 → 返回失败结果（不抛错使父任务悬停）；子 loop 的 on_state 回调复用父 run_id 回显 UI（RunLog 用子 id）。
11. **记忆事实纠正**：memory 工具的真正改动点是 runtime `_execute_agent_tool` 分发（loop 侧本来就允许调用）。

### 7.2 建议修改项（采纳）

- **chat_tools 保持单轮无状态协议**：工具结果嵌入下一轮 observation JSON（不追加 tool message 链），避免原生/JSON 双模式历史格式冲突；多 tool_calls 在单轮内自然成立。
- **TokenMeter 图片配额**：每张图按固定 850 tokens 计入预算。
- **ContextBudget 优先级（飞行域修正）**：指令 > 当前观察/world_state > 最近结果 > tool_cards > 早期历史 > memory/guidance。context_window 复用 `infer_model_capabilities`。
- **重试安全**：loop 级重试仅限 `error_code=TIMEOUT` 且工具 ∈ READ_ONLY_TOOLS ∪ {task_status}；CONNECTION 类交给既有 `_retry_after_reconnect`（不叠加）；重试前先 `drone_get_status` 回读防双重位移。
- **failure_count 保持窗口累计语义**（成功不清零，recoverable 重试成功不计入），回归测试固化。
- **error_code 完整映射**（not connected / heartbeat 丢失 / unknown tool / validate 异常 / async 非终态无 task_id 等全部覆盖）+ 单测。
- **output_schema 校验 normalize 后形状**（raw 保留供诊断）；D 阶段只做 VLM 两工具 + drone_get_status + 规则回退高频工具。
- **run_trace API** 与 RunLog 同阶段落地；子 agent 只用于开放解释类任务，不替代确定性 skill。
- **golden corpus 行为基线**：`tests/test_behavior_baseline.py`，命令清单 → intent/route/plan 输出固化，每阶段 diff。
- **执行顺序调整**：F（规则收敛）提前到 A1 之后立即做；A1 与批量执行语义合并为同一阶段（消除依赖倒置）；run_log 先定 version 与脱敏。
- **frame 新鲜度**：observation 增加 `frame_age_s`（`_last_visual_frame` 记时间戳）、`status_age_s`；`_has_recent_image` 增加时间窗。
- **对话上下文进 loop**：`decide_next_step` 注入 `conversation_context`（复用 `_recent_chat_context`）。
- **多机语义**：批量与子 agent 默认单机（vehicle_name 来自 params），多机只走 plan 路径。

### 7.3 修订后的实施顺序

| 阶段 | 内容 | 验证 |
|---|---|---|
| A0 | llm_protocol.py（validate_json_schema / TokenMeter / ContextBudget / tool schema 合成器 / 图片配额） | 新增单测 |
| B | run_log.py（version/脱敏/保留策略）+ AgentLoop/Runtime 接入 + run_trace API | 单测 + 回归 |
| F | 规则收敛：extract_intents + 统一 target_class + golden corpus 基线 | 新单测 + 回归 |
| A1 | chat_tools 原生调用（OpenAI/Anthropic）+ native_tools 能力标志 + 单轮无状态协议 + 批量 tool_calls 解析 | 单测（mock HTTP）+ 回归 |
| G1 | 批量执行语义：parallel_actions 执行 + per-action guard + 单次批量审批 + 失败中止 | 单测 + 回归 |
| C | 任务契约：MissionPlan.goal + loop verify_success + 一次纠正动作 | 单测 + 回归 |
| D | 上下文预算接入 decide_next_step/plan（飞行域优先级 + 图片配额） | 单测 + 回归 |
| E | 工具层：error_code 完整映射 + output_schema（VLM 等）+ 安全重试（仅 TIMEOUT 读工具） | 单测 + 回归 |
| G2 | 记忆主动化：facts/transcript/recall + runtime 分发 + 卡片注入优先级 | 单测 + 回归 |
| H | 子 agent：decide_next_step override + SubAgentRunner + agent_subtask + 递归限制 + UI 回显 | 单测 + 回归 |
| J | 全量回归 + 评判 Agent 终审 | pytest 全绿 |

## 8. 实施状态（2026-08-16）

全部阶段已实现，测试从基线 147 个增长到 **274 个，全绿**。新增/修改：

- 新增模块：`src/agent/llm_protocol.py`（schema 校验/token 计量/上下文预算/工具 schema 合成）、`src/agent/run_log.py`（事件溯源日志）、`src/agent/sub_agent.py`（子 Agent）
- 改造模块：`llm.py`（原生工具调用+批量解析+任务契约解析+上下文预算）、`agent_loop.py`（批量执行+完成判据验证+帧新鲜度）、`tool_executor.py`（error_code+输出校验+安全重试+重连不重发飞控）、`runtime.py`（RunLog 接入+memory 工具+子 Agent 工具+轨迹重建）、`command_slots.py`（统一意图词表）、`planner.py`（goal 合成）、`memory.py`（facts/transcript/recall）、`loop_types.py`（parallel_actions/verification_status/frame_age_s）
- 新增测试 9 个文件：协议层、日志、意图基线、任务契约、工具错误、记忆、子 Agent

### 8.1 终审（评审 Agent）结论与修复

终审确认：**审批门、急停、守卫链路均未被绕过**（安全底线保住）；发现 8 个必须修复项，全部已修复并补测试：

| 编号 | 问题 | 修复 |
|---|---|---|
| M1 | status_ok 判据可被空转绕过 | 改为"至少一次成功动作且无失败" |
| M2 | 无遥测时验证误判失败 | 无法评估的判据 → unevaluated warning，不置失败 |
| M3 | 纠正动作/批量结果在轨迹重建中丢失 | 纠正决策入审计轨迹；`_plan_from_loop_state` 按工具名消费配对 |
| M4 | INVALID_TOOL_OUTPUT 是死设计 | 输出校验真正拦截（ok=False + 错误码），VLM 在 normalize 后校验 |
| M5 | 批量白名单混入 airsim_task_cancel | 移出批量白名单 |
| M6 | 重连后盲目重发飞控指令（双重位移风险） | 飞控工具重连后**永不自动重发**，返回失败+人工确认提示 |
| M7 | RunLog 截断产生非法 JSON、事件类型不全 | 超长行写 truncated 标记事件；补 plan/observation/verification 事件 |
| M8 | 帧新鲜度未实现（过期画面触发目标确认） | `_FRAME_MAX_AGE_S=60` 时间窗 + observation.frame_age_s |

另有 10 项建议改进已采纳（golden corpus 精确断言、图片 token 去重计、NED z 归一、子 Agent 禁用飞控工具、幻觉工具名跳过不终止、协议降级事件落盘、执行槽检查覆盖特殊工具等）；已知限制：RunLog 保留策略在 list 时触发、plan/decision 输出沿用规范化兜底（未加 schema 硬校验）。

### 8.2 对比三方项目的达成情况

- **deepseek-harness 方向**：事件溯源日志（RunLog）✓、原生工具调用 ✓、token 计量+预算 ✓、输出校验 ✓、子任务机制 ✓、严格测试 ✓（274 个）
- **agenticros 方向**：memory 主动工具 ✓、任务转录 ✓、统一意图契约 ✓（extract_intents）、错误即数据（error_code）✓
- **openclaw-swarm 方向**：确定性技能优先 ✓（skill 层保留）、LLM 高层意图+确定性执行 ✓（任务契约验证）
- **领域超越点**（三者都没有的）：任务契约机器验证（完成判据）、帧新鲜度时间窗、飞行工具重连不重发、真机审批+急停体系

## 9. 编队功能域设计（多机协同，2026-08-16）

### 9.1 目标与定位

补上多机协同缺口：**编队飞行 + 区域覆盖任务**。设计遵循 openclaw-swarm 验证过的分层——
**LLM 发高层意图（一次工具调用），确定性控制环执行微观协调（10Hz 速度闭环）**——但全部挂在本系统
已有的安全体系上：审批、急停、任务契约、RunLog、error_code。

明确边界（与本系统原则一致）：
- 支持 **AirSim 与 PX4 MAVLink 后端**（PX4 通过鸭子类型速度控制协议：OFFBOARD 进入/保持/退出 + 心跳新鲜度，
  详见第 10 节）；PX4 ROS2 后端因 HTTP bridge 速度语义未验证暂不注册
- 编队控制环只发**速度指令**，且每 tick 续发（duration=0.2s）——线程死亡时无人机自动悬停（AirSim 速度指令到期即停），天然失效保护
- 编队模式激活期间，**单机飞控工具被拦截**（`drone_fly_to` 等返回 BLOCKED"编队激活中"），防止两个控制路径打架

### 9.2 模块：`src/modules/formation.py`

**纯函数（可单测，无 IO）**：
- `formation_offsets(formation_type, count, spacing) -> list[(x, y, z)]`：line / v_shape / triangle / diamond / square / hexagon / circle / arrow，虚拟结构偏移表
- `plan_coverage(area, resolution, partition, path_algo, drone_ids) -> dict[id, waypoints]`：网格化 → 分区（balanced 轮询 / stripe 条带 / quadrant 象限）→ 路径（boustrophedon 牛耕 / spiral 螺旋 / nearest 最近邻贪心）

**`FormationController`（依赖 FlightController 接口，注入真实 AirSim 控制器或测试假件）**：
- 模式：`idle / formation / coverage`；线程 10Hz（可配置），`start()/stop()/pause()`
- tick：逐个 `controller.get_status(name)` → 按模式算速度（formation：目标=中心+偏移，P 控制 Kp=1.5、限速 5m/s；coverage：航点 + 0.5m 到达判定 + 3m 减速区）→ `controller.move_by_velocity(vx,vy,vz,0.2,name)`
- 动作：`set_drones / set_formation / takeoff(altitude) / move_center / rotate(deg) / scale(factor) / land_all / hover_all / start_coverage / stop`
- `status()`：mode、每机 {id, position, target, airborne}、`stable`（全部距目标 < 0.5m）、coverage 进度
- **安全钩子**：`should_stop` 回调（急停/取消）→ tick 检测到即 hover_all + idle + 停线程；连续 50 tick 错误自动停机
- 失败语义：takeoff/land 逐机执行，任一失败 → 该机报错并整体返回失败（含已成功机列表），由 Agent 决策是否继续

### 9.3 工具集成（ToolRuntime）

- `ensure_ready` 尾部：后端为 airsim / px4_mavlink 时注册 `formation_command` 工具（调用时要求
  ≥2 架机；闭包路由到 FormationController，控制器懒创建；PX4 经鸭子类型速度协议走 OFFBOARD）
- 工具签名：`formation_command(action, formation_type?, spacing?, altitude?, x?, y?, z?, angle_deg?, scale_factor?, area?, resolution?, partition?, path_algo?, vehicle_ids?)`
- 返回：`{status, mode, stable, drones[], progress?}` + 人类可读摘要；失败带 error_code
- **风险挂钩**：card risk=medium；`_tool_risk_level` 扩展：formation_command 的飞行类 action（takeoff/move_center/rotate/scale/land_all/start_coverage）+ real_vehicle → high → 走既有审批门
- **急停**：runtime 传入 `should_stop=lambda: supervisor.is_emergency_stopped() or _cancel_requested.is_set()`；`blocked_by_supervisor` 时工具调用本身也被 execute 拦截
- **单机冲突拦截**：formation 模式非 idle 时，`_execute_agent_tool` 对 CONTROL_TOOLS 单机工具返回 BLOCKED（drone_hover/land/status/task_cancel 豁免）
- 工具卡片 + `skills/formation/SKILL.md` 引导文档（setup→takeoff→move/coverage→轮询 stable→land 的工作流）

### 9.4 任务契约

- `VERIFY_METRICS` 增加 `formation_stable`：扫描结果中 `formation_command(status)` 返回 `stable=True`
- 纠正动作：重新 `formation_command(action="status")` 轮询
- LLM 计划可声明 `{"metric": "formation_stable"}`；规则规划器不生成编队计划（编队是 LLM 级任务，规则不兜底）

### 9.5 测试

- 纯函数：8 种队形 offsets 的数量/对称性/间距；覆盖规划的分区公平性、路径覆盖全部 cell、航点数量
- 控制环（FakeController 记录调用）：P 控制收敛方向、限速、coverage 航点推进与减速、50 错停机、should_stop → hover
- 工具：registration 门控（airsim/px4_mavlink）、action 路由、单机冲突拦截、风险等级
- 契约：formation_stable 判据与纠正

### 9.6 不做的事（明确边界）

- 不做机间避碰（仿真默认安全；真机编队需额外研究，本文档只保证速度闭环与急停）
- 不做"每机一个 Agent 协商"（集中确定性控制是正解，openclaw-swarm 已验证）

### 9.7 实施状态与终审修复（2026-08-16）

全部实现，测试 321 个全绿（新增 test_formation.py 31 + test_formation_contract.py 16）。终审（评审 Agent）确认
守卫链闭环，发现 5 个必须修项，全部已修复并补测试：

| 编号 | 问题 | 修复 |
|---|---|---|
| M1 | 自适应 velocity duration 无上限，破坏"线程死亡→自动悬停" | duration 硬上限 1.0s（失控距离 ≤5m） |
| M2 | start/stop 线程竞态可致双线程并发指挥 | start() 先 join 旧线程 + generation 令牌，旧线程循环条件失效 |
| M3 | 急停被 20s RPC 锁 + 长 takeoff 阻塞 | hover 改为 fire-and-forget（发送即返回），急停不再排队 |
| M4 | coverage 区域不过地理围栏 | validate 对矩形四角/圆形最远点做围栏校验（danger 拦截） |
| M5 | GCS 直调 tools.execute 绕过冲突守卫 | 守卫下沉到 ToolRuntime.execute（单点覆盖所有调用方） |

建议修 S1-S6 亦全部采纳：schema 补全 6 个缺失参数并统一 spacing≥2m、coverage_start 要求至少一机 airborne、
发送循环逐机复查 mode、formation_stable 只取最后一条 status（+输出 schema）、shutdown 清 coverage 状态且
激活中禁止重新配置、shutdown 的 was_active 语义修正。

已知残留风险（文档化）：tick 内单个 RPC 仍可能被 AirSim 控制器 20s 锁等待阻塞（hover 已不 join，
最坏 20s 单次），急停延迟有界但非零；编队仅 AirSim 仿真后端（真机 PX4 offboard 不在本设计）。

## 10. 编队后端统一：PX4 MAVLink 支持（2026-08-16）

### 10.1 目标

让 `formation_command` 在 px4_mavlink 后端同样工作，skill 文档与 LLM 编排零改动。
关键事实（代码核实）：
- FlightController 接口后端无关，`move_by_velocity(vehicle_name)` 三条链路都已实现；
- **但 MAVLink 的 `_move_by_velocity_one` 是"阻塞流式 + 结束后退出 OFFBOARD"的单次语义**
  （duration min 0.8s，尾部 `_finish_offboard_position_hold` 切回 LOITER）——与编队环
  "每 tick 发一个 setpoint、立即返回、下一 tick 续发"完全冲突，不能复用；
- PX4 速度指令只在 OFFBOARD 模式生效，且需要连续 setpoint 流（断流触发 failsafe）。

### 10.2 鸭子类型协议（控制器可选方法，FormationController 用 getattr 探测）

| 方法 | AirSim | PX4 MAVLink |
|---|---|---|
| `send_velocity_setpoint(vx,vy,vz,vehicle_name)` | 缺省 → 回退 `move_by_velocity`（带 duration） | 新实现：逐 sysid 发**单条** SET_POSITION_TARGET_LOCAL_NED，立即返回 |
| `prepare_velocity_control(vehicle_name)` | 缺省 → 跳过 | 新实现：逐 sysid 停 hold 线程 + set_mode(OFFBOARD) |
| `is_velocity_control_active(vehicle_name)` | 缺省 → 跳过 | 新实现：逐 sysid 检查 current_mode == OFFBOARD |
| `release_velocity_control(vehicle_name)` | 缺省 → 跳过 | 新实现：OFFBOARD 时 `_finish_offboard_position_hold`（回 LOITER） |

### 10.3 FormationController 集成

- tick 发送：有 `send_velocity_setpoint` 走它（PX4 路径），否则 `move_by_velocity(duration)`（AirSim 路径）
- takeoff 成功后（mode=formation 前）：逐机 `prepare_velocity_control`，任一失败 → 不激活编队并报错
- tick 每机发送前：`is_velocity_control_active` 存在且返回 False（RC 抢占/模式被切）→ hover_all + idle + 事件 `mode_lost`
- hover_all / land_all / shutdown：逐机 `release_velocity_control`（MAVLink 的 `_hover_one`/`_land_one` 本身已处理 OFFBOARD 退出，此为双保险）
- OFFBOARD 断流窗口：进入 OFFBOARD 到首条 setpoint 的间隙受 PX4 `COM_OF_LOSS_T`（默认 1s）约束，编队 tick 启动 <200ms，安全；文档化

### 10.4 注册门控与安全

- `_ensure_formation_tools` 门控扩展为 `backend_id in {"airsim", "px4_mavlink"}`；调用时仍要求 ≥2 机
- validate() / NOT_CONNECTED / 重连不重发 / 冲突守卫 / 审批 / 急停 全部沿用（后端无关）
- 本轮不做 px4_ros2：bridge 的 velocity 端点是 HTTP 阻塞语义，10Hz×N 机开销未验证，列为后续
- SITL 验证路径：单链路多 system（已有支持）——一台 PC 跑 N 个 PX4 SITL，`list_vehicles` 返回 px4_sysN

### 10.5 测试

- FormationController（fake PX4 控制器实现 4 个鸭子方法）：prepare 调用、走 send_velocity_setpoint、
  mode 丢失 → hover_all+idle+事件、hover_all/shutdown 调 release
- 注册门控：px4_mavlink 注册、px4_ros2 不注册（本轮）
- MavlinkController 新方法（沿用 fake _Link/_Message 模式）：send 单条到各 sysid、prepare 置 OFFBOARD、
  is_active 读模式、release 回 LOITER

### 10.6 评审修复与 SITL 注意事项（2026-08-16）

评审发现并已修复：
1. **M1 既有硬伤——多机 sysid 透传断裂**：内部 `set_mode()`/`_finish_offboard_position_hold()`/
   `dict(self._position)` 不带 vehicle_name 时落到第一架。修复：`_position/_velocity/_gps_origin`
   属性改为 target 优先（`_target_sysid()`）；所有逐机 one-shot 路径改用 `_set_mode_one()`；
   `_target_sysid()` 拒绝 sysid=0（初始化簿记表）
2. **M2** `_hover_one` 低空路径 NameError（`self.stop(vehicle_name)` 未定义）→ 内联当前目标停止逻辑
3. **M3** coverage 从不进入 OFFBOARD → `coverage_start` 启动前逐机 prepare（失败回滚已 prepare 的机）
4. **M4** 断链静默成功 → `send_velocity_setpoint` 断链返回 False；tick 对发送失败计数（auto-stop 生效）；
   `is_velocity_control_active` 增加心跳新鲜度（>2s 视为失联）
5. prepare 部分失败回滚（对已进入 OFFBOARD 的机显式 release）
6. 工具门控错误文案改为后端无关

SITL 验证要点（真实 PX4 SITL 踩坑记录）：
- 每个 SITL 实例必须独立 `MAV_SYS_ID`（默认全 1 会并入同一张 sysid 表）
- 单链路汇聚需要 mavlink-router；显式设置 `COM_OF_LOSS_ACT=0 (Hold)`、核对 `COM_OF_LOSS_T`
- 编队 takeoff → OFFBOARD → move → 模拟 RC 抢占验证 mode_lost → hover；断链验证 COM_OF_LOSS 行为

## 11. 可靠性修复轮（2026-08-16 第二轮评判后）

对四个第三方项目（agenticros / deepseek-harness / openclaw-swarm / NemoClaw）源码级评判后，
确认并修复了以下可靠性缺陷（全部有回归测试）：

### 11.1 编队错误计数语义（P0，bug）

`FormationController.consecutive_errors` 此前是**累计**计数（从未在成功后重置），与"连续 N 次
错误自动停机"的语义不符——长任务中偶发瞬时错误会在几分钟后误触发全队停机。openclaw-swarm 在
每次成功轮询后清零（control-loop.ts:230）。修复：tick 内统计本次错误，全成功则清零（含
per-drone 计数），只有连续失败才累计到阈值。

### 11.2 stop() 自线程 join（P0，bug）

auto-stop / mode_lost 路径在 tick 线程内调 `stop()`，`thread.join()` 对当前线程抛
`RuntimeError("cannot join current thread")`，被 runner 的 blanket except 吞掉。修复：join 前
排除当前线程。新增真实线程驱动的 auto-stop 测试（此前测试同步驱动 tick 永远碰不到此路径）。

### 11.3 急停/取消抢占阻塞飞行命令（P0，安全）

此前急停悬停排在阻塞命令后面：PX4 `_move_to_position_one` 最长 `distance/velocity+8s`，
AirSim `moveToPositionAsync().join()` 阻塞到 RPC 超时——急停期间无人机继续飞向目标。
修复：控制器新增 `set_stop_provider()`，阻塞循环（move/takeoff/rotate/stream/prime）逐次检查
外部停止信号，退出时走安全 hold 路径（OFFBOARD→LOITER）；AirSim 用可中断等待 + fire-and-forget
hoverAsync 抢占。运行时把 supervisor 急停/取消信号注入两个后端。

### 11.4 LLM 传输层重试（P1）

此前 LLM 调用零重试（planner 层只有消息子串匹配的部分重试，429/5xx 不覆盖）。修复：
`_request_with_retry` 对 429/5xx/URLError 指数退避 + 抖动重试（≤3 次，尊重 Retry-After 且封顶），
4xx（含 context overflow）不重试——重试无意义。

### 11.5 上下文超限恢复（P1）

此前 provider 报 `CONTEXT_WINDOW_EXCEEDED` 直接失败。修复：`is_context_overflow_error` 检测 +
`_chat_with_overflow_recovery` 用减半预算重建请求重试一次（plan / decide_next_step 的 JSON 与
native 两条路径）。对齐 deepseek-harness 的 overflow-recovery 思想（无模型裁剪→压缩→重试的第一级）。

### 11.6 控制环可观测性与失效安全界（P1）

- 自适应速度时长上限从 1.0s 收紧到 0.4s（死线程最坏漂移 5m → 2m@5m/s）；方向不变：
  tick 越慢时长越大是失效放大，现在封顶。
- 新增 `tick_metrics`（tick_count / avg / max / dropped_ticks）与 `drone_errors` per-drone 计数，
  status() 暴露。
- 漂移补偿自调度（对齐 openclaw control-loop.ts:135-156）：`_schedule_next` 把下一次 tick 锚定到
  原始节拍而非本次 tick 结束；落后超过一个周期时按 floor 记账 `dropped_ticks` 并重置到
  now+period——循环永远不会用背靠背的 tick 突发追赶。

### 11.7 其他

- `AgentMemory._save` 加锁（潜在双写者防护）；tmp+rename 原子写不变。
- 修正三处过时文档（tool_cards / manifest / SKILL.md / 设计文档 9.1）——仍写"编队仅 AirSim
  后端"，会误导 LLM 在 PX4 上放弃编队工具。

### 11.8 测试增量

`tests/test_formation_reliability.py`（8）：成功重置 / 瞬时错误不误停 / 连续失败自停 /
线程级 auto-stop 干净退出 / per-drone 计数 / tick 指标 / 自线程 stop / 时长封顶。
`tests/test_flight_preemption.py`（4）：阻塞 move 被 stop 抢占 / prime 中断 / 默认语义不变 /
velocity move 中断后安全退出 OFFBOARD。
`tests/test_llm_retry.py`（8）：429 重试成功 / 5xx 耗尽 / 4xx 不重试 / URLError 重试 /
Retry-After / overflow 标记 / 减半预算重建 / 非 overflow 透传。

全量测试：335 → **355 通过**。

### 11.9 编队故障策略：全队停机 vs 单机隔离（设计决策）

openclaw-swarm 在单机遥测失败时**继续带健康机飞行**（逐机错误隔离，airsim-client.ts:247-281），
本系统保留**全队自动停机**（连续失败达阈值 → hover_all + 停环）。这是有意的安全取舍：

- 多旋翼编队中，失去遥测的无人机位置未知——继续编队可能与失控机碰撞；
  地面站场景下"停住等状态确认"比"继续飞"更安全。
- openclaw 的场景是 20Hz 轮询 + 模拟器，失败通常是瞬时的；我们面对真机链路（PX4 心跳新鲜度
  已检测），丢链 = 必须停。
- 可观测性补齐：`drone_errors` 逐机计数已暴露在 status()，运维能看出"哪架机在掉链子"，
  单机持续故障会在 10Hz×N 个 tick 内触发停机而非静默带病飞行。

若未来需要"故障机脱离编队、健康机继续"（类似 openclaw），改动点已收敛：tick 的
auto-stop 分支改为 per-drone 判定 + 从 `drone_ids`/`offsets` 摘除故障机，其余不变。
