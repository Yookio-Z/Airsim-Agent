# 项目评估与改进路线

> 状态：评估报告（2026-09-24，基于 commit `29fd182` + 工作区未提交改动）
>
> 本文是一次外部代码评审的完整记录，包含现状评估、问题清单和分阶段改进路线。
> 按 [`docs/README.md`](README.md) 的维护规则第 3 条，本文内容属于"评估结论"，
> 不替代 [`system_upgrade_plan.md`](system_upgrade_plan.md) 的路线主线定位。
> 采纳本文建议时，实际优先级仍以 `system_upgrade_plan.md` 为准。

> **后续说明（2026-09-24，本次会话）**：本文快照之后，`src/agent/runtime.py`
> 已按本文第 2 步的方向拆分：8078 行 / 212 方法 → 711 行组装点 + 13 个领域模块
> （`execution` / `agent_bridge` / `events` / `cancellation` / `vehicle_api` /
> `approval_gate` / `guardians` / `session_api` / `replay_api` / `gcs_api` /
> `run_state` / `settings_store` / `session_store`）。
>
> 因此**本文正文里的 `runtime.py:<行号>` 引用已全部失效**（行号都超过新文件
> 长度），请按符号名而非行号检索。另外两处正文描述已被本次改动取代：4.2 节
> 「实时包线看门狗硬编码 8m/70m」现已按任务分档并可配置（`guardians.py` +
> `config.py`），「`no_fly_zones` 运行时恒为空」现已接通配置与执行路径。
> 新的模块布局见 `project_structure.md`。

---

## 一、总体结论

**这个项目的代码质量明显高于同类开源项目，但工程纪律没有跟上代码规模。**

核心链路——Agent 循环、工具执行、安全校验、取消审批——做得相当扎实，很多细节能看出是
踩过真实事故后修出来的（注释里保留了大量"为什么这样写"的事故记录）。真正的风险不在
"哪里写得不好"，而在"没有任何机制强制把已经很好的地方保持住"：

- 没有 CI，所有质量门禁靠人工自觉执行；
- 四个文件合计约 2 万行，最大单文件 8016 行；
- 约 1600 行安全/自主代码已写好但从未接入运行时，而 README 已在宣传这些能力。

改进顺序应当是**先固化基线、再消除"写了没接线"的部分、最后重构巨型文件**——不是反过来。

---

## 二、规模与现状快照

以下数据为实测（`git worktree` 隔离后复现）：

| 项目 | 数值 |
|---|---|
| Python 源码 | 约 48,300 行（`src/` 约 4.4 万 + `scripts/` 约 4,300） |
| 前端静态资源 | 约 21,000 行（CSS 7,561 + 8 个 JS 模块 + HTML） |
| 测试代码 | 约 11,800 行，46 个 pytest 文件 + 2 个 Node `.cjs` |
| 测试结果 | **629 passed / 7 failed**（83.6s，Windows，项目 venv） |
| HEAD 基线对照 | 同样 7 个测试在 `29fd182` 上是 **9 failed** — 未提交改动未引入新失败，且少 2 个 |
| CI | **无**（无 `.github/`、无任何流水线配置） |
| 覆盖率门禁 | `.coveragerc` `fail_under = 45`，仅 include 12 个关键文件 |
| 未提交改动 | 18 个文件修改（+1,324 / −164）+ 4 个新文件 |

### 各模块规模

| 目录 | 行数 | 说明 |
|---|---|---|
| `src/agent/` | 22,281 | 编排中心，Agent 循环与 LLM 客户端 |
| `src/modules/` | 14,194 | 飞控后端实现（AirSim / MAVLink / ROS2）、安全、编队、感知 |
| `scripts/` | 4,338 | 含 `person_actor.py` / `person_control.py` 两个独立工具 |
| `src/autonomy/` | 1,763 | **绝大部分未被 runtime 引用** |
| `src/gcs/` | 1,498 | 地面站服务门面 |
| `src/ui/` | 1,186 | 后端 HTTP/SSE 服务（不含静态资源） |

### 最大的文件

| 文件 | 行数 | 风险 |
|---|---|---|
| `src/agent/runtime.py` | 8,016 | **上帝对象**，248 个方法，混合 10+ 种职责 |
| `src/modules/mavlink_controller.py` | 4,404 | 协议编解码主路径 |
| `src/agent/llm.py` | 3,927 | 模型注册 + 能力推断 + 重试 + 流式 + 规划 + fallback |
| `src/agent/tool_executor.py` | 3,455 | 工具治理、安全校验、错误分类 |
| `src/ui/static/ui-chat.js` | 2,991 | 思考流渲染占近 1,000 行 |
| `src/ui/static/ui-map.js` | 2,987 | 地图与航点 |
| `src/modules/airsim_controller.py` | 2,058 | AirSim RPC 适配 |
| `src/agent/agent_loop.py` | 1,651 | **质量最高的文件，不建议重写** |

---

## 三、做得好的地方

### 3.1 Agent 循环是全项目最亮的部分

`src/agent/agent_loop.py` 实现了"LLM 规划一次 → 按计划游标顺序执行 → 仅在观察偏离计划
假设时才转 ReAct"的双层结构：

- **计划游标**（`agent_loop.py:165`、`agent_loop.py:221-251`）：规划一次后按序执行，避免
  每步 15–40 秒的模型往返。
- **偏离检测**（`_plan_step_deviation`, `agent_loop.py:1033-1071`）：检出"0 目标"、
  "视觉确认未通过"、"目标丢失"三类偏离。
- **机器可验证完成判据**（`_verify_completion`, `agent_loop.py:1129-1220`）：支持 9 种
  metric（`status_ok` / `planned_motion_steps_ok` / `photo_taken` / `target_confirmed` /
  `landed` / `flying_at` / `position_reached` / `mission_progress_complete` /
  `formation_stable`），并自动为含运动步骤的计划注入 `planned_motion_steps_ok`
  （`agent_loop.py:967-980`）。**这直接堵住了 agent 最常见的"幻觉成功"漏洞。**
- **确定性收敛**（`agent_loop.py:1110-1127`）：计划内实质步骤全部成功过即结束，不等 LLM 收尾。
- **异步任务终态收敛**（`_settle_async_result`, `agent_loop.py:1505-1569`）：轮询到终态，
  超时取消，并处理了"当前后端无 `airsim_task_status`"的解耦。

这套设计超过大多数开源 agent 框架的水准。

### 3.2 LLM 降级链处理得很完整

`llm.py:1731-1855` 的双协议降级：native function calling → 端点拒绝时降级 JSON-schema
提示模式 → 上下文溢出时用 0.5 倍预算重试 → 规则 fallback。`_decision_from_tool_calls`
还特意把**空响应判为错误而非完成**（`llm.py:1976-1986`），因为那是内容过滤/拒答的典型形状。

### 3.3 取消与审批的细节考虑周全

这些不是设计文档能想出来的，是被卡过才知道的：

- 取消**绑定到具体 run_id**（`runtime.py:4410-4420`），防止上一个任务的取消旗标泄漏给下一个；
- 取消时**主动解锁暂停**（`runtime.py:4376`），否则暂停轮询会永久占着执行槽；
- LLM 往返途中（15–40s）被暂停，则**丢弃模型返回的动作**重新观察（`agent_loop.py:286-297`）；
- 审批等待期间同时检查急停和取消，且**批准到达瞬间再复核一次**（`runtime.py:2262-2318`）。

### 3.4 注释记录的是"为什么"而不是"是什么"

这是国内少见的工程文档水准。随手三例：

- `safety_validator.py:104-110` — 解释 NaN 为什么能躲过所有阈值比较（NaN 与任何阈值比较
  都返回 False，于是一路"通过"）；
- `formation.py:352-356` — 解释 0.4s 指令时长的失效保护含义（0.4s × 5m/s ≤ 2m 漂移）；
- `config.py:37-42` — 解释 AirSim 端口为什么从 41452 改成 41451。

全项目**零 TODO / FIXME / NotImplementedError**。1746 个函数中 692 个有 docstring，
且复杂路径几乎全覆盖。

### 3.5 并发控制的防事故设计

- **飞控串行闸门**（`_control_gate`, `tool_executor.py:329`，30s 超时）：注释记录了
  真实事故——视觉伺服和 Agent 两路线程同时向 PX4 推不同目标点，"历史上表现为突然
  俯冲/乱转/掉高"。
- **编队代际计数**（`formation.py:321`、`formation.py:337-344`）：每次 start bump
  generation，卡死的旧 tick 线程无法在重启后继续指挥机群。
- **编队漂移补偿自调度**（`formation.py:341-366`）：下一次 tick 锚定原始节拍而非本次末尾。
- **编队连续错误语义**（`formation.py:832-844`）：完全成功的 tick 清零计数，瞬时错误不会
  在几分钟后误触发 auto-stop。

### 3.6 测试中锁定了真实缺陷

`tests/test_safety_and_cancel_guards.py` 文件头逐条列出 12 个历史缺陷并一一对应测试，
且断言的是**语义**而非实现细节：

- 断言"参数未被静默改写"——`result.params["z"] == 10.0` 仍为 10.0，只是
  `suggested_params` 给出建议（`test_safety_and_cancel_guards.py:128-140`）；
- 断言"danger 级违规必须阻断而非夹紧"（`:128`、`:143`）；
- 断言取消绑定 run_id，防止 A 运行的取消影响 B 运行（`:436-448`）；
- 断言"暂停后取消在 5s 内释放执行槽"（`:385`）。

这类测试的回归保护价值远高于覆盖率数字。

### 3.7 前端重构方式规范

从 11,983 行单文件拆为 8 个模块，采用"只搬运不改代码 + `node --check` 逐文件校验 +
逐行 diff 比对"的方式，是教科书式安全重构。

思考流渲染（`ui-chat.js`）在 DOM 重建 vs 就地打字机的性能权衡、折叠状态恢复、
a11y（用原生 `<details>` 而非 div onclick）、合成标签过滤上都有明确注释论证。

### 3.8 服务端安全细节扎实

- 静态服务路径穿越防护（`server.py:1034-1040`）；
- 请求体上限 18MB（`server.py:529`），JSON 必须为 object（`:536`）；
- 序列化有界化 `_bounded_copy`（`:586`），防病态 LLM 输出触发 `RecursionError`；
- SKILL.md 写入"先写 tmp → parse 校验 → finally 删 tmp → 再落盘"（`:970-983`）；
- Windows 断连识别 10053/10054/10038（`:51-69`）；
- 本次未提交改动新增了 `runtime.shutdown()` 优雅关闭（`:1172-1180`），
  修复 daemon 线程被硬杀在飞控指令中间的问题。

### 3.9 记忆机制设计有想法

`src/agent/memory.py` 的 `_update_skill_candidate` 会从历史工具序列签名自动提炼技能
候选，成功率 ≥0.8 且 runs ≥3 时产出 `preferred_skills` + `routing_hints`——这个
"从历史自动归纳技能"的闭环不错。中文召回用 bigram 切分免 tokenizer（`:360-370`）。
并发防护也考虑到了：`_sanitize` 逐行校验（`:94-121`）、损坏文件备份为 `.corrupt_<ts>.json`
（`:74-90`）、带 pid 的临时文件 + `replace`（`:123-135`，注释说明固定 `memory.tmp` 在
双进程下会互相覆盖且 Windows 上 replace 会失败）。

---

## 四、明确的短板

### 4.1 完全没有 CI（最高优先级）

无 `.github/`、无 `.gitlab-ci.yml`、无 `Jenkinsfile`、无 `Makefile`、无 pre-commit。
`pyproject.toml` 配了 black（line-length=100）和 mypy（`disallow_untyped_defs = true`），
`.coveragerc` 设了 `fail_under = 45`——但这些在没人手动敲 `pytest --cov` 时都是摆设。

对一个能连真机、且能在 localhost 上无鉴权下发飞控指令的项目，这是最该先补的缺口。

### 4.2 "写了但没接线"的安全代码（信任问题）

| 问题 | 位置 | 后果 |
|---|---|---|
| `no_fly_zones` 运行时恒为空 | `safety_validator.py:41` 定义，`tool_executor.py:330-337` 构造时不传，全项目无赋值 | 线段-禁飞区相交检测（`:73-100`）写得完整但**从未在真实飞行中生效** |
| `SafetyArbiter` 未接入 | `src/autonomy/safety_arbiter.py` 123 行，零引用；`:115-117` 还留有 `pass` 占位 | 实时状态仲裁层不存在；电量检查只在此处有实现 |
| `PolicyEngine` / `WorldState` 未接入 | `policy_engine.py` 440 行等 | 约 1,600 行死代码 |
| `belief_state.py` / `obstacle_avoidance.py` / `semantic_search.py` 未接入 | 合计约 1,220 行 | 同上 |
| 编队闭环不走 validator | `formation.py` 10Hz 闭环只在工具调用时查一次参数 | 闭环内部无独立包线监控 |
| 实时包线看门狗硬编码 | `runtime.py:7268-7327`，8m 高度 / 70m 水平 | 不可配置，且只在 `flight_control` 任务上启动 |
| `validate_and_execute` 装饰器无调用点 | `safety_validator.py:440-549` | 死代码，且内含语义混乱的 warning 分支 |
| README 已在宣传未接入的能力 | README "Autonomy & safety" 段落 | **宣传与实现不符** |

对飞控项目而言，宣传与实现不符比缺功能更危险——它会让人以为有第二道防线。

### 4.3 四个巨型文件

`runtime.py`（8,016 行 / 248 方法）同时承担：配置读写、附件存储、会话 CRUD、RunState
状态机、事件发布、审批、包线看门狗、追踪辅助、Replay、工具治理、记忆绑定、GCS 门面构造。
`_verify_run_outcome` 单个方法就有 286 行。

`llm.py`（3,927 行）把模型注册表、能力推断、JSON 提取、重试、流式、tool 协议、prompt
构造、fallback 规则、schema hint 全塞在一个类里。

### 4.4 `ToolRuntime._lock` 是全局单锁

`execute()` 全程持锁，跨越整个工具调用（包括 `drone_takeoff` 这类阻塞数十秒的操作），
导致只读回读 `drone_get_status` 也被阻塞。项目当时的应对是**禁用"回读快路径"**
（`runtime.py:1084-1087` 有注释自认"实测 20s+ 超时"）——这是在绕开问题而不是解决问题。

### 4.5 无执行硬超时，无线程池

- `_plan_and_execute` 的 worker 线程**没有硬超时**。LLM 规划一次 15–40s，纠正循环最多
  28 步，理论上一个任务能跑十几分钟而只有 cooperative cancel 能打断。
- 每个任务一个 daemon 线程（`runtime.py:1213`），`self._thread` 单引用；旧线程句柄
  只能靠 `_execution_slot` 追踪。
- 存在执行槽（`runtime.py:852`）和飞控闸门（`tool_executor.py:329`），这两个设计是对的，
  但都由 Semaphore 隐式维持，没有承载硬超时和优先级的专用 worker。

### 4.6 服务端无鉴权

`POST /api/tool` 能直接下发飞控指令，`POST /api/command` 能让 LLM 起飞，
`/api/models/{id}/reveal-key` 明文回传 API key（`server.py:743`）。无 CORS 配置、
无请求限流。对 localhost 服务尚可辩护，但 README 明确支持真机部署。

### 4.7 `server.py` 是超长 if 链

`do_GET`/`do_POST`（`server.py:96-523`）约 40 个端点全在方法体里，加一个端点就要改方法体，
分发逻辑完全不可测试。`RUNTIME` 是模块级全局（`:47`），所有 handler 直接引用，
无法在测试中注入替身。SSE 每连接一个阻塞线程（`:598-618`），客户端多时线程线性增长。

`server.py` 1,185 行**零 Python 测试**。

### 4.8 测试替身与实现强耦合

- `test_safety_and_cancel_guards.py:86-116` 的 `_shell_runtime()` 手工注入 **25 个属性**；
- `test_tool_errors.py:20-37` 和 `test_agent_execution_foundation.py:38-108` 各写一份
  runtime 工厂，加上 `test_safety_and_cancel_guards.py:45-64` 的 `_rt()`，共 **三份重复**；
- `object.__new__` 绕过 `__init__`；
- `test_safety_and_cancel_guards.py:329-428` 用大量 `lambda ...: None` 打桩近 20 个方法。

运行时加一个必需属性，这几个测试会以**难以诊断的方式**失败。这是 4.3 的直接后果。

### 4.9 测试缺口

| 缺口 | 说明 |
|---|---|
| `src/ui/server.py` | 路由分发、SSE 循环、瓦片代理、路径穿越防护全无覆盖 |
| `src/config.py` | 零直接测试，env 前缀解析、`extra="ignore"`、类型转换均未验证 |
| `src/agent/runtime.py` | 只用 `object.__new__` 局部打桩测十几个私有方法；**`submit_command` → 计划 → 执行 → 事件流无集成测试** |
| `src/agent/llm.py` | 只测 protocol 层 helper，真实 HTTP 客户端、超时/重试/流式解析无端到端测试 |
| `src/gcs/services.py` | 本次新增的急停门禁（`:377-387`）**无测试**，尽管测试文件头把它列为已锁定的缺陷 |
| `src/modules/mavlink_controller.py` | 编解码主路径靠 `FakeMav` 桩 |
| 端到端 / 集成 | **一条都没有**，`scripts/verify_p*.py` 是 gitignored 的手动冒烟脚本 |
| 前端 | 21,000 行仅 2 个 `.cjs`（只测 `ui-camera.js` 两个函数） |

### 4.10 依赖与配置管理

- **配置三分裂**：`DroneConfig`（环境变量 / `.env`）、`src/data/settings.json`（UI 设置）、
  `ModelRegistry`（模型 API key）互不相干。后端选择优先级（CLI > `AIRSIM_AGENT_BACKEND` >
  settings.json > 默认）**只写在 `server.py:1149-1154` 的代码里**，README 和 config.py 都没记。
- **`DroneConfig` 是 import 时求值的模块级单例**（`config.py:101`），测试无法注入不同配置——
  这也是那几个手搓 runtime 工厂的间接原因（`test_perception_axis.py:66` 只能自己写假类）。
- `ultralytics`（拖 torch）被列为**运行时直接依赖**而非可选组。
- `pytest-asyncio` 在 dev 依赖里，但全项目没有一个 `@pytest.mark.asyncio` — 死依赖。
- 8 个前端 JS 模块 + vendor 进仓的 MapLibre（803KB）**完全没有依赖清单或版本锁定**。
- `pyproject.toml:48-50` 项目 URL 仍是 `https://github.com/yourusername/airsim-controller` 占位。
- `config.py:33` 的 `ros_workspace_path: str = "$HOME/ws_px4"` 用了字面量 `$HOME` 而非展开。

### 4.11 文档缺口

README 质量不错（有 ASCII 架构图，明确强调"LLM 不进入高频飞控回路"这个关键原则），
但缺少：

- 安装前置（PX4 SITL / AirSim / Unreal 项目）；
- **测试运行说明** — 完全没提 `pytest` 怎么跑、`.cjs` 怎么跑（`node --test`）；
- `DRONE_*` 环境变量与 `.env` 说明；
- 故障排查。

`test_agent_execution_foundation.py:1125` 的 `__main__` 手工测试清单引用了一个已重命名的
函数（`test_visual_question_forces_capture_then_vlm_analysis` → 现为 `..._frame_inspection`），
直接 `python tests/test_agent_execution_foundation.py` 会 `NameError`（pytest 收集不受影响）。

### 4.12 默认端口不一致

`src/tools/core.py:84` 的 `drone_connect` 默认 `port=41452`，
而 `config.py:42` 是 `airsim_port=41451`。后者注释解释了这是历史 bug（AirSim 实际监听
settings.json 里的端口），但 `core.py` 的默认值没同步修正。

### 4.13 `PersonActor` 已测试但未产品化

`scripts/person_actor.py`（972 行）质量很高——模块 docstring 记录了 2026-09-21 在
Blocks 场景的实测结论（约 600 pose writes/s、teleport 不触发 AnimBP 走跑状态、真实足部
动画需要 UE 侧 movement component）。`tests/test_person_actor.py` 的 20 个用例覆盖了
运动学语义（走 4m 必须 >1.2s、加速度斜坡、刹车距离、步态不偏移轨迹）。

但 `src/agent/tool_cards.py` 里**零个 `person_` 工具**，`src/tools/` 下也没有。它只被手动
CLI `person_control.py` 使用——测了一条尚未接入产品的旁路。而且该测试文件有 12+ 处
`time.sleep()`，天然 flaky 且慢。

---

## 五、分阶段改进路线

### 第 0 步：拿到一个可信的基线（1 天）

**先拿到绿灯，再动任何代码。**没有可信基线，后面所有重构都是赌博。

1. **定性那 7 个失败测试。**初步观察：
   - `test_formation_px4::test_mavlink_send_velocity_setpoint_fails_when_disconnected`、
     `test_formation_contract::test_formation_conflict_exempts_hover_land` — 断言
     `controller._connected = False` 后 `send_velocity_setpoint` 仍返回 `True`，疑似
     构造 fixture 未真正置位或存在共享状态；
   - `test_connection_health` 3 条 — 在 HEAD 上也失败，其中 2 条本次改动后已通过，
     看起来有顺序/环境相关性，需要确认是否 flaky；
   - `test_camera_backend_guard::test_px4_backend_refuses_camera_execution`、
     `test_flight_preemption::test_airsim_wait_move_arrival_preempts_on_stop` — 需逐条
     判断是真回归还是测试本身过期。

   定性为 flaky 的加 `xfail` + 注明原因，定性为真 bug 的修掉。

2. **加 GitHub Actions。**Windows runner 跑 `pytest`（这几个测试疑似对 Windows 路径/时序敏感，
   Linux 上"绿"了也不算数），Linux 上跑 black / mypy check / `node --test`。

3. **修依赖可收集性。**`test_camera_raw_capture.py`、`test_camera_stabilization.py`、
   `test_person_actor.py`、`test_tool_manifest_contract.py` 四个模块在未装 `airsim` 时
   collection error。改成 `pytest.importorskip("airsim")`，
   并考虑把 `airsim` 从运行时直接依赖移到 optional extra。

**产出**：一份 CI 绿灯 + 失败测试定性报告。

---

### 第 1 步：把"写了但没接线"的部分二选一（2–3 天）

这是**关乎信任而非关乎代码**的一步。

**1.1 让 `no_fly_zones` 真正生效**

- 在 `config.py` 增加可配置的禁飞区列表（先支持 NED 平面圆和矩形即可，够用且易实现）；
- `ToolRuntime.__init__`（`tool_executor.py:330-337`）构造 `FlightConstraint` 时传入；
- UI 设置面板给一个编辑入口（可以先只支持配置文件，不急着做 UI）。

已有的线段穿越检测代码（`safety_validator.py:73-100`）直接就活了。

**1.2 把 `_envelope_guard_loop` 升级成真正的包线看门狗**

现在它在 `runtime.py:7268-7327`，8m/70m 硬编码，只在 `flight_control` 任务上启动。
改造为：

- 读 `DroneConfig` 的安全包线配置（配置层在本次未提交改动里已经建好了，
  `config.py:52-61`）；
- 全任务类型常驻，不只 `flight_control`；
- 把 `no_fly_zones` 检查也塞进去；
- 顺带把 `SafetyArbiter` 里能用的部分（电量检查、链路状态判断）上提进来。

**1.3 对 `src/autonomy/` 做减法**

- `SafetyArbiter` 中已实现且可用的部分上提进看门狗；
- 其余（`PolicyEngine`、`WorldState`、`NavigationSkill`、`TrackingSkill`、
  `belief_state.py`、`obstacle_avoidance.py`、`semantic_search.py`）移到
  `docs/design/`，文件头明确标注"未接入的设计稿"；
- **或者**，如果确实要保留在 `src/`，就在 `autonomy/__init__.py` 和 README 里写明
  "本包未接入运行时"。**中间状态最危险。**

**1.4 明确 `PersonActor` 的定位**

在 README 里如实写它是"仿真行人 actor 的独立开发工具（手动 CLI），未接入 Agent 主链路"。
要么接成工具（`tool_cards.py` 加 `person_*` 组 + `tool_executor` 里走安全校验），
要么就承认是 dev tool。

**1.5 补 `services.py` 急停门禁的测试**

`src/gcs/services.py:377-387` 新增了"急停时拒绝启航"，但测试文件头已把它列为锁定缺陷，
实际用例缺失。补上。

---

### 第 2 步：拆 `runtime.py` —— 先挖测试缝，再搬文件（1–2 周）

**这是最大的技术债，但顺序很重要。**

#### 2.1 先挖缝（不改行为）

三个测试文件各写了一份 runtime 工厂，`_shell_runtime()` 手工注入 25 个属性。这种测试在
重构时会产生一堆"与改动无关"的失败，诊断成本极高。

做法：引入显式的构造分层——`AgentRuntime.__init__` 只做依赖注入，字段初始化挪到
`__post_init__` 风格的方法；测试改用"传真对象 + 替换少量方法"而不是"注入 25 个属性"。

**这一步只改结构不改行为，现有测试应全绿。**它是后续所有搬文件工作的前提。

#### 2.2 按职责切分

| 抽出模块 | 承担 |
|---|---|
| `SettingsStore` | settings.json / .env 读写 |
| `SessionStore` | 会话 CRUD、附件、历史 |
| `EventBus` | 事件发布与 SSE 订阅者管理 |
| `Guardians` | 包线看门狗、连接健康、追踪辅助 |
| `RunCoordinator` | execute 路径的状态机（承载第 1 步升级后的看门狗） |

`RunCoordinator` 部分会顺带做一件现在没有的事。

#### 2.3 用专用 worker 线程 + 队列替掉"每任务一 daemon 线程"

现在 `self._thread` 是单引用，旧线程句柄只能靠执行槽追踪，且 `_plan_and_execute`
**没有硬超时**。

改成专用 worker + 队列后：

- 硬超时有了天然的落点（不再只能靠 cooperative cancel）；
- "执行槽"从隐式 Semaphore 变成显式的 worker 状态；
- 旧线程句柄丢失的问题消失。

现有的执行槽（`runtime.py:852`）和飞控闸门（`tool_executor.py:329`）**设计是对的，保留**，
只是承载方式变了。

#### 2.4 `llm.py` 拆得保守一些

把模型注册表、能力推断、JSON schema hint 构造分离出去，重试/流式解析留在原地。

> **不重写降级链。** `llm.py:1731-1855` 的 native → JSON schema → 规则 fallback
> 降级是本项目最精妙的代码之一，重写只会变差。

---

### 第 3 步：并发与安全的小修（穿插在第 2 步中进行）

单点改动、收益明确，随时可做：

- **修 `ToolRuntime._lock` 的粒度。** 改成读写锁或按工具分锁。项目当初是绕开问题
  （禁用快路径），现在应该解决问题。约两小时，能直接消掉一类 20s+ 超时。
- **校验与执行之间加状态复核。** 现在是"取状态 → 校验 → 执行"三步，中间有窗口。
  飞控指令应在真正下发前再确认一次未被急停/暂停打断。
- **HTTP 加最简鉴权。** 启动时生成 token 打到 console，所有 mutating 端点
  （`/api/tool`、`/api/command`、`/api/models/*/reveal-key`）要求
  `Authorization: Bearer`，并显式 bind `127.0.0.1`。不做完整登录体系。
- **`server.py` 的 if 链换成路由表。** 40 个端点改成
  `dict[(method, path)] → handler`，路由本身可单测，处理器按域拆文件。
  顺带解掉模块级全局 `RUNTIME` 不可注入的问题。
- **`_envelope_guard_loop` 拆到 `Guardians`**（第 1 步已完成升级，此处只是搬家）。

---

### 第 4 步：前端 —— 不加构建步骤，但把隐式契约变显式（1 周）

**保留"零 npm 依赖"这个决定。** 它对这个项目是自洽的：MapLibre vendor 进仓、
8 个 JS 直接 `<script>` 引入，没有构建就没有构建坏掉的那一天。

但共享全局作用域 + 加载顺序是**隐式契约**，`index.html:665` 的注释就是在给这个契约打补丁。

- **把每个模块包进 IIFE 显式注册到一个 `App` 命名空间。** 加载顺序变成声明的而不是靠
  约定的；跨文件引用会立刻暴露出来。
- **补文档。** 显式写清后端选择优先级（CLI > 环境变量 > settings.json > 默认）、
  `DRONE_*` 全部环境变量、`.env` 用法、后端热切换的语义。这部分现在只存在于代码里。

#### 前端测试策略

沿用 `.cjs` 里已有的好办法（在 `vm` 里加载真实实现，只补最小依赖），但规模化：

- 抽纯函数为可测单元：`screenToNed`、`haversineMeters`、`smoothParagraphTarget`、
  `nodeCategory`、`isSyntheticToolCallLabel`；
- SSE 状态机单测（`snapshot` / `message_update` / `run_update` 的合并逻辑）。

21,000 行只有 2 个 `.cjs` 太少，但也不需要上 Jest——纯逻辑 + vm 加载真实文件就够了。

---

## 六、明确不会做的事

- **不重写 Agent 循环。** `agent_loop.py` 的计划游标 + 偏离检测 + 9 种机器可验证判据
  优于大多数开源 agent 框架，重写只会变差。
- **不上 FastAPI / React / Vite。** 现在 stdlib `ThreadingHTTPServer` + 原生 JS 是自洽的
  选择，引入框架会带来新的构建和部署负担，换不到什么。
- **不补全整个 autonomy 层。** 除非确定要让这套系统真机长期自主飞行，否则 1,600 行的
  策略引擎是负债不是资产。
- **不追求覆盖率数字。** `.coveragerc` 只 include 12 个关键文件、门限 45% 是务实选择——
  `mavlink_controller` 的编解码主路径本来就要靠真机验证。覆盖率在这里不是好指标。
- **不引入数据库。** sessions / memory / task_runs 的 JSON 存储在当前规模（missions 50、
  lessons 30、runs 50）完全够用，SQLite 会增加并发写这一整类问题。

---

## 七、优先级前三

| 优先级 | 事项 | 理由 |
|---|---|---|
| **1** | 加 CI + 定性那 7 个失败测试 | 没有可信基线，后面全是赌博。成本最低、收益最高 |
| **2** | 让 `no_fly_zones` 生效，包线看门狗升级为读配置、常驻 | 写好的安全代码从未运行过，这比缺功能严重 |
| **3** | 拆 `runtime.py`（先挖测试缝） | 直接消掉最大可维护性风险，也是解决测试脆弱的前置条件 |

**如果只能做一件事**：第 0 步的 CI + 测试定性。它是其余所有工作的放大器。
