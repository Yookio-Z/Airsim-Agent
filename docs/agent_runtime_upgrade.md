# Agent 运行时升级（对照 pi 的 runtime loop）

> 状态：设计稿
> 目的：把"执行为什么出问题"讲清楚，并给出可分批上线的升级路线
> 参照实现：`C:\Users\26494\Desktop\pi`（Pi Agent Harness，TypeScript）——`packages/agent/src/agent-loop.ts` 与 `harness/runtime/*`
> 关联：[`agent_architecture.md`](agent_architecture.md)、[`agent_harness_design.md`](agent_harness_design.md)、[`agent_tool_skill_refactor.md`](agent_tool_skill_refactor.md)

## 0. 结论

我们的执行问题**不是模型不够聪明，是运行时缺四个结构**。按"不做的代价"排序：

1. **循环与观察者之间没有隔离边界** —— 任何在 UI 回显路径上抛的异常都会直接杀死整个任务（2026-10-12 那次任务就是这么死的：一个格式化辅助函数签名写错，Agent Loop 第一步出结果时崩，界面只显示"任务处理失败"）。
2. **完成判定是模型的"声明"，不是"事实"** —— `LoopDecision.is_complete` 由模型自报；崩溃之后系统还替它写了一句"任务已完成…一切正常"。
3. **上下文每轮重建、工具结果折进 JSON 观察** —— 谁忘了把某个事实写进重建逻辑，模型就是瞎的（实测感知信息整段缺失）。pi 的结构让这类错误不可能发生：**模型观察到什么，什么就是对话里的一条消息**。
4. **没有批次执行、没有压缩、没有恢复** —— 多步任务每步一次 LLM 往返（实测每次 15~40s），27 米抵近要 4~5 步；进程一挂任务就没了。

## 1. pi 的运行时循环长什么样

低层循环（`agent-loop.ts`）是一段**极短、极稳定**的代码，全部机制都是注入的回调：

```text
runLoop:
  outer: while true                        # 兜住"agent 本要停下时又来了 follow-up"
    inner: while hasMoreToolCalls or pendingMessages
      1. prepareNextTurn(上一轮)           # 可换模型/思考等级，可替换整个上下文（压缩入口）
      2. 注入 steering 消息                # 操作员中途插话，不打断任务
      3. streamAssistantResponse()         # 流式，逐 token 发射 message_update
      4. stopReason == error/aborted → agent_end（干净收尾）
      5. 取出这一条的**全部** toolCall，作为一个批次执行
         - stopReason == "length" 时整批判失败（截断的参数不许执行）
         - terminate 全为 true 才停止
      6. 结果 push 进上下文（真实 toolResult 消息）
      7. shouldStopAfterTurn?(...) → 干净收尾
    followUpMessages 有 → 继续；否则 break
```

上层的 harness（`harness/runtime/*`）再叠一层**可持久化的状态机**：13 个显式状态、JSONL 事务日志、按 operation id 恢复、非幂等效果在重放时标 `INTERRUPTION_MARKER`。

关键设计选择（逐条都有对照价值）：

| 机制 | pi 的做法 | 出处 |
|---|---|---|
| 观察者隔离 | 监听器抛异常 → 捕获并发射 `handler_error` 事件，**循环不死** | `harness/events.ts:104-120` |
| 完成判定 | **没有模型自报的 done 标志**。完成 = 没有更多 toolCall（或整批 `terminate`）+ 应用层 `shouldStopAfterTurn` 谓词 | `agent-loop.ts:222-255` |
| 上下文 | 上下文**就是**消息转录，结果作为真实 `toolResult` 消息追加 | `agent-loop.ts:237-240` |
| 压缩 | 阈值触发（`contextWindow - reserveTokens`）→ LLM 摘要 + 保留最近 `keepRecentTokens` 原文 + 被丢弃/被读文件清单；切点避开 tool-call/result 成对边界 | `harness/compaction/compaction.ts` |
| 工具批次 | 一条 assistant 消息里的**所有** toolCall 一起执行（`sequential`/`parallel` 按 run 配置），无"每轮一个工具"限制 | `agent-loop.ts:409-489` |
| 工具失败 | `executePreparedToolCall` 内 try/catch → `isError: true` 的**结果**，不让 run 失败 | `agent-loop.ts:708-714` |
| 工具动态增删 | 工具结果可带 `addedToolNames`，**持久地**加入该 lane 的可用工具集（技能激活即展开子工具） | `harness/runtime/drive/tool-placement.ts:204-215` |
| 操作员插话 | 每轮轮询 `getSteeringMessages()`；停下后再看 `getFollowUpMessages()` | `agent-loop.ts:168,257-266` |
| 取消 | 持久化 `control.status = cancel_requested` → 按状态叶子分别 reconcile，能恢复的恢复、能中止的中止 | `harness/runtime/drive/reconcile.ts:147-172` |
| 预算 | **run 级没有步数上限**；只有重试上限与"顺序批次转移计数"上限 | 见 §1 引用 |
| 观察接口 | 类型化事件流（`run_start`/`turn_start`/`tool_start/update/end`/`compaction_*`…）+ `lane.watch()` 增量快照 | `harness/agent-harness.ts:255-373` |

## 2. 逐项对比

| 维度 | pi | 我们（`src/agent/*`） | 差距性质 |
|---|---|---|---|
| 循环与观察者 | 监听器异常 → `handler_error` 事件 | `_event`/`_notify_state` **裸调回调，无 try/except**（`agent_loop.py:1625-1633`） | **致命** |
| 完成判定 | 结构性（无 toolCall）+ 应用谓词 | 模型自报 `is_complete`（`loop_types.py:46`）；`_verify_completion` 仅在计划声明了判据时才跑，且"无法评估→按完成接受" | **致命** |
| 失败后的最终答复 | 由运行结果生成 | 崩了之后仍可能输出"任务已完成" | **致命** |
| 上下文 | 真实转录 + 压缩 | 每轮重建 payload：`observation`(JSON) + 最近 5 条决策/结果 + tool_cards + skill_guidance + memory（`llm.py:1771-1788`） | 结构 |
| 工具结果 | 真实 `toolResult` 消息，全文（工具自己截断/落盘给 path+offset） | 折进 JSON，`_trim_data` 1200 字符上限，带图则压成摘要 | 结构 |
| 每轮观察内容 | 转录里全部 | `_compact_observation` 只给 step_index/时间/帧龄/backend/drone/last_action_result（**不含感知**） | 结构 |
| 多工具/轮 | 一条消息内全批（可选并行） | 只读工具可并行；飞行指令**硬性一轮一个** | 有意为之，但太慢 |
| 模型调用 | 原生 tool_calls + 流式 | 自研 JSON 决策协议、单轮、不流式（execute 路径） | 效率 |
| 压缩 | 阈值 + LLM 摘要 + 保留尾部 | 无摘要；`_fit_sections` 按优先级截断/丢弃，70% 窗口预算 | 结构 |
| 工具集 | 动态 `addedToolNames` 持久展开 | 静态 `capabilities` 掩码（换后端时整批裁剪） | 结构 |
| 操作员插话 | 每轮轮询 | 仅 `route_strategy == agent_loop` 时接受 steer | 部分等价 |
| 取消 | 持久状态 + 逐叶子 reconcile | 旗标 + 若干检查点（`agent_loop.py:170,286,1519`），取消后悬停 | 部分等价 |
| 预算 | 无步数上限，靠结构性终止 | `max_steps` 16~28 → `blocked` | 需要保留（见 §4） |
| 持久化/恢复 | JSONL 事务日志 + 按 operation id 恢复 + 中断标记 | 单进程内存态；进程挂了任务就没了 | 结构 |
| 审批/许可 | 核心里**没有**（用 `before_tool` 钩子做） | 有，位于工具执行**之前**（`agent_bridge.py:559-585`） | **我们更对** |

## 3. 为什么我们的执行会出问题（四次事故 → 四个根因）

### 事故 A：2026-10-12 任务崩在第一轮

`run.failure_reason = "AgentBridgeMixin._format_loop_result_body() takes 1 positional argument but 2 were given"`。
崩点在**回显路径**上（`agent_bridge.py:361` 漏了 `@classmethod`，调用方是 `self.xxx(data)`）。因为回调和循环在同一个调用栈且没有隔离，异常直接穿透 `AgentLoop.run` → `_run_correction_loop`（也没有 try/except）→ `_plan_and_execute` 的兜底 except → 任务标记失败。

**根因 1：循环与观察者没有边界。** 在 pi 里这条路径是：`watcher` 捕获 → 发 `handler_error` 事件 → 任务继续。

### 事故 B：完成判定的三个真实缺口

（更正：最初以为"崩了之后报告成功"——查证后不成立。失败路径本来就是诚实的
（`任务处理失败: <异常>`），那句"任务已完成…一切正常"是会话里更早一条指令的正常回答，
我看错了消息顺序。真正的问题在下面三处。）

1. **空声明可以过关**：模型 `is_complete=true`、且一个动作都没执行、计划也没给判据时，
   `_verify_completion` 直接返回 `satisfied: True`（`if not criteria: return satisfied`），
   运行被判 completed。`status_ok` 这个判据的注释本来就写着"空循环声明完成不能过关"，
   但它只在计划自带判据时才被评估（`tests/test_task_contract.py` 里那条
   `test_loop_ignores_completion_gate_when_no_criteria` 记录的正是这个洞）。
2. **收敛路径绕过判据**：计划步骤都成功后走"确定性收敛"，原实现直接
   `verification_status = verification_status or "ok"`——计划里写了"必须确认落地"这类判据，
   收敛路径从不评估它。"动作都执行过"不等于"目标达成"。
3. **判据无法评估 = 视为通过**：`_verify_criterion` 在缺遥测时 `satisfied=True, evaluated=False`，
   只在 reason 里留一句 warning。于是"没有证据"和"已验证通过"在外层看不出区别。

**根因 2：完成是"声明"不是"事实"**（部分成立：模型自报 + 一层机器判据，但判据有三个洞，
且没有把"未验证"作为独立状态暴露出来）。pi 里根本没有模型自报的 done：没有 toolCall 才是完成，
且应用层还能再插一个谓词。

### 事故 C：27 米抵近要 4~5 步，每步 15~40 秒

实测（见本仓库 2026-10-12 的实飞记录）：`distance 27.7 → 26.8 → 20.0 → 11.1 m`，每步约 6 米（步长还被"前方障碍"夹住），每一步都是一次完整的 LLM 往返；execute 路径的决策是**不流式的 JSON 单轮**，期间操作员看不到任何进展。

**根因 3：上下文与交互模型不适合多步任务。** pi 一轮可以发多个 toolCall（一条消息里的全部调用成批执行），且流式输出让等待可解释；我们的"一轮一个工具 + 不流式 + 每轮重建大 payload"把一次 4 步任务放大成 4 次长等待。

### 事故 D：Agent 不知道画面里有什么

我们花了一轮才发现 `_compact_observation` 根本没把 `perception` 放进模型可见的观察里——模型必须先花一整轮调用去问，而这份状态每轮都在内存里。

**根因 4：重建式上下文要求"有人记得把每个事实写进去"。** pi 的结构里，模型观察到的任何东西都是一条消息，天然在上下文里；不存在"忘了加"这种 bug。

补充（同一族的第 5 个根因，暂列观察项）：进程重启后运行不可恢复。真机上"崩溃/重启"发生在 `drone_fly_to` 执行中间时，我们必须能从持久状态判断"那次移动到底发出去没有"。

## 4. 能搬的 / 不能搬的

**照搬**：
- 观察者隔离（根因 1）——成本极低，收益极大。
- "完成必须是事实"（根因 2）——`_verify_completion` 从"可选"改成"有运动步骤则必须"，并让终态决定最终答复的措辞。
- 上下文 = 转录 + 压缩（根因 3/4）——这是最大的一次结构改动，但它一次性消掉一整类 bug。
- 一条消息内多工具、按 run 配置串/并行（只读批次）。
- 阈值压缩 + 保留尾部 + 工具自己截断并给"重读句柄"（path/offset）。
- 工具动态展开（技能激活 → 其子工具变为可用），替代静态 capability 掩码 + 每轮全量 tool_cards。

**不能照搬（我们是对的一方）**：
- **run 级无步数上限**：编码代理可以无限跑，飞行器不行。保留硬上限，但补"无进展检测"，避免 28 步都空转。
- **任意并行批次**：文件读可以并行，`drone_*` 绝不能。"一轮一个飞行指令"是安全约束，保留。
- **可重放的效果**：pi 认为幂等的效果可以重放；飞行指令不可重放。恢复时必须先回读遥测再决定，不能自动续做。
- **核心无审批门**：我们有，而且位置正确（执行前）。保留。
- **13 状态持久机**：现阶段照搬属于过度工程；先取"可恢复 + 中断标记"这个结论，不取整套状态机。

## 5. 升级路线

### P0 止血：隔离 + 诚实（已完成，见 §7）

1. **循环里给观察者加边界** ✅：`AgentLoop._event` / `_notify_state` 捕获异常并记录；state 通道坏时用事件通道公告一次（`kind=handler_error`）；两者都坏也只落日志，任务继续。失败按运行计数、有上限、不递归。
2. **完成判据补三个洞** ✅：新增兜底判据 `any_action_succeeded`（拦住"零动作声明完成"）；收敛路径现在必须过一遍计划声明的判据，不满足就交回上层；判据无法评估时状态标 `unverified` 并带明细。
3. **报告口径随证据** ✅：报告开头是运行时结论（`_outcome_headline`），模型正文接在后面；判据被否决时不得使用"任务完成"措辞；总结末尾附"回显出错 N 次"这类保留。
4. **状态语义不动** ⚠️：循环层 `status` 仍表示"模型请求收尾"，机器否决记在 `verification_status`，由运行层的 failed-verification gate 落地（这是既有契约，`test_loop_rejects_completion_until_criterion_satisfied` 记录在案，不去动它）。

验收：观察者抛异常时任务照常跑完并在时间线上出现 handler_error 公告（`tests/test_agent_loop_isolation.py` 9 条）；零动作声明完成、收敛绕过判据、判据无法评估三种情形都被如实标注。

### P1 上下文与会话（3~5 天）

5. **转录式上下文**：工具结果作为真实消息进入转录；每轮 payload = （压缩点之后的）转录 + 小的动态抬头（telemetry/perception 摘要按需）。
6. **压缩**：阈值触发、LLM 摘要 + 保留最近 N tokens 原文 + 关键清单（读过的文件/目标），切点避开 tool-call/result 边界。
7. **工具动态展开**：技能激活返回 `addedToolNames`，子工具在该运行内生效并持久；tool_cards 只给当前可用的，payload 随之变小。

验收：同一任务在模型可见信息等价的前提下，payload 体积下降；人为忘掉某个字段也不会再让模型失明（因为它是消息）。

### P2 循环效率与可中断（3~5 天）

8. **只读批次**：一条消息内允许多个只读 toolCall（`perception_status`+`airsim_detect_objects`+`drone_get_status` 一轮拿到），执行顺序与结果顺序对齐；飞行指令仍一轮一个。
9. **原生 tool_calls + 流式**：execute 路径改用提供商的原生工具调用与流式输出（先做流式，再换协议；两者可分开上线）。
10. **steer/cancel 升为一等状态**：每轮必查 steering，取消做成持久状态 + 逐阶段 reconcile。

验收：一次"读状态→起飞→连抵近 3 步→悬停"的任务，模型往返次数下降，且等待期间操作员能看到流式进展。

### P3 持久化与恢复（1~2 周，真机前完成）

11. **运行持久化**：JSONL 事务日志 + 按 run id 恢复；未完成且非幂等的效果标 `interrupted`，恢复后**先回读遥测**再决定，绝不自动重发飞行指令。

验收：在 `drone_fly_to` 执行中间 kill 进程，重启后能从日志判定"该次移动不确定"，并给出明确的人工确认入口。

## 7. 本轮已完成 / 未做

### 7.0 闭环断点的修复（2026-10-12 实机任务）

操作员任务："你看得到那个蓝色衣服行人距离多远嘛？你飞过去靠近它后降落"。结果 961 秒、
飞机一步没动。逐层查证如下：

| 证据 | 结论 |
|---|---|
| `perception_status`：`detect_fps=0`、`detector={}`、`targets=0` | YOLO **从未启动**，不存在锁定目标 |
| 技能指导 `target-inspection` / `region-search` 的 `subtools` **不含 `perception_start`** | 模型按指导书办事，压根不会去开检测——它只调了 `airsim_detect_objects`（读）和 `inspect_current_frame`（VLM） |
| 同一帧单帧实测：`person` 类 conf **0.83/0.78/0.75**（框高 30~38px） | 目标**完全可以被锁**：不是太小、不是置信度不够 |
| 默认类别是 `car`（`.env` 未设 `DRONE_PERCEPTION_TARGET_CLASS`） | 即使当时开了检测，找的也是车，锁不到人 |
| `drone_approach_target` 连拒三次，消息只有一句"先检测/对准再抵近" | 拒绝理由不可行动：模型分不清"检测没开"和"开了但没锁"，重试三次后放弃 |

根因是**可发现性**，不是能力：VLM 能"看见"，但动作工具只认检测锁定，而没有任何一处告诉
模型"锁定需要先开检测、并且要用对类别"。

修复（四处）：
1. `airsim_detect_objects` 现在返回 `detection_running` 与 `hint`——`count=0` 的两种原因
   （检测没开 / 开了但没找到）从此可分。
2. `drone_approach_target` 的拒绝拆成两个可行动的 reason：`detection_not_running`
   （→ 先 `perception_start`）与 `no_locked_target`（→ 转向搜索或换类别），并带上当前类别。
3. 两份技能指导补上 `perception_start`；`region-search` 增加"Precondition — detection must
   actually be running"，`target-inspection` 增加 `Rule 0 — a lock is the price of movement`
   （点明"视觉模型看见 ≠ 锁定"，禁止重试被拒的调用）。
4. （上一轮已修）`perception_start(target_class=…)` 在轴已运行时能真正切换类别。

回归测试：`tests/test_perception_view.py` 新增两条（`detection_running` 语义、拒绝理由分型）。

**仍未解决**：单次任务 961~1335 秒。闭环能走通了，但每步决策 15~40 秒的静默仍是体验硬伤
（见下方"未做 1"）。


**已完成（P0）**：观察者隔离 + 完成判据三个洞 + 报告口径。改动集中在
`agent_loop.py`（隔离、兜底判据、收敛验证、报告口径）、`loop_types.py`（unverified
状态与明细字段）、两个测试文件（`test_agent_loop_isolation.py` 9 条新增，
`test_task_contract.py` / `test_attempt_summary.py` 三处随契约更新）。全量 771 项通过。

**已存在、无需新做**（对照时以为要补，查证后本来就有）：
- 一条消息内多工具 + 只读批次：`parallel_actions` 的解析（native 与 JSON 两条路）与
  `_execute_batch_actions` 都在，提示词也明确允许"最多 2 个独立只读调用同批"，
  并禁止把飞行指令放进批次。
- 原生 tool_calls：`decide_next_step` 已优先走原生 function calling，模型不支持时才降级
  JSON-schema。

**未做（按优先级）**：
1. **决策过程流式**（P2 剩余）：`on_reasoning` 的管线在 plan / final_answer / chat 三条路
   都有，唯独 `decide_next_step` 没有——execute 循环每步 15~40s 全程静默。需要给决策调用
   加流式（含原生工具调用路径）并把回调接到 `AgentLoop.run` → 运行层。因为要动 llm.py 的
   调用核心、且必须重启实机验证，本轮没有赶。
2. **P1 上下文改为转录 + 压缩**：最大的一次结构改动，消掉"忘了写进 payload 就失明"这一类。
3. **P3 运行可恢复**：JSONL 事务日志 + 中断标记，真机前完成。

**建议顺序**：先做 1（见效快、与实机体验直接相关），再做 2（结构收益最大，但要配套
压缩策略与回归），最后 3。

## 6. 明确不做

- 不引入 pi 的 13 状态状态机与 lane/operation 分层（现阶段收益不抵复杂度）。
- 不取消飞行指令的一轮一个限制，也不允许自动重放飞行效果。
- 不因为"pi 没有步数上限"就删掉我们的上限；改成"上限 + 无进展检测"。
