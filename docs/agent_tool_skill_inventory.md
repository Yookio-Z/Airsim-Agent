# 工具集与 Skills 清单（整理 + 选型规则）

> **现状（2026-10-12 决议之后，本节为准；下面的 §1–§4 是决议前的现场记录）**
>
> **决议**：放弃 YOLO 目标检测。理由是开放集语义指令（"蓝色门框后的黄色方块，
> 穿过门框落上去"）用固定类表检测不可扩展，而"检测锁定"曾被写成飞行动作的
> 前置条件——那正是 2026-10-12 任务失败的根因。新的分工：**语义问视觉模型，
> 距离问深度图，坐标问点位投影**。
>
> **工具面变化**
> - 删除：`perception_start`、`perception_stop`、`airsim_detect_objects`、
>   `drone_approach_target`（含 ToolRuntime 里的抵近实现与四个 helper）。
> - 新增：`airsim_locate_point(x, y)` —— 归一化点 + 深度 + 位姿 → 距离与世界
>   坐标（NED），只读、纯几何。
> - `perception_status` 变成**纯相机健康**（online / fps / capture_age_s / view /
>   画面尺寸 + 事件），不再返回目标快照。
> - 视觉模型的答案 schema 增加粗尺寸：`bbox_hint {x, y, w, h}`（归一化，粗值，
>   用于算距离、朝向与"够近"）。
>
> **运行时变化**
> - 守卫不再注入任何"靠近"动作；它注入的决定一律标 `source="guard"`（UI 显示为
>   🛡 运行时守卫），无法替代模型动作时返回空动作 + `needs_replan=True`——
>   **交回模型**，并在时间线上显示"运行时交回模型重新决策（原因…）"。
> - 守卫的理由不再混进"模型思考"事件（只有 plan/guard 之外的决策才发思考事件）。
> - 计划偏离判据里**不再有"必须锁定"**：`drone_move_relative` / `drone_fly_to`
>   这类动作没有任何检测前置条件，运行时只保留飞行安全（包线/高度/围栏）。
> - 系统提示的选型规则改写为"看得到 → VLM 取点 + 深度 + 有界相对移动"，
>   `airsim_locate_point` 用于需要绝对坐标时。
>
> **技能变化**：`visual-approach` 成为唯一的靠近路径（不再是"锁不住时的退路"），
> 新增"穿过门洞"与"落在小物体上"两节；`target-inspection` 的 Rule 0 改为
> "一次看 + 一次深度是移动的代价"；`region-search` 的扫视改为有预算的 VLM
> 扫视（一处一次、每位置 ≤8 个朝向、最多 3 个位置）。
>
> **代价（如实记录）**：① 搜索/跟踪显著变贵（每次"看"10–20 s，且没有跨步身份，
> 要周期性重问"还是同一个吗"）；② 避障从代码保证降级为技能纪律（前向移动前用
> 深度多点采样做净空判断）；③ 世界坐标只在目标可见且被调用 `airsim_locate_point`
> 时才存在，丢了目标就没有坐标锚点。
>
> 回归用例：`tests/test_no_detection_approach.py`（无检测、无 perception_axis，
> 用 VLM+深度+有界移动走完整条"门框 → 方块"接近链；以及工具面残留检查）。

> 目的：把"工具调用混乱、功能重复"这件事落到可执行的清单上——每个工具干什么、哪一个和哪一个重叠、什么场景该选哪个、技能之间怎么衔接。
> 起因：2026-10-12 实机任务失败排查（守卫静默改写模型选择 + 思考行来源混淆 + 工具/技能重叠）。
> 关联：[`agent_tool_skill_refactor.md`](agent_tool_skill_refactor.md)、[`agent_runtime_upgrade.md`](agent_runtime_upgrade.md)

## 0. 先回答三个具体问题

**① 中间那条"模型思考"是真的模型调用吗？** 分两种，之前**混在一起显示**了：

- **真·模型思考**：plan 阶段是流式真实 token；execute 循环里是模型写在 `reason` 字段里的文字。
- **不是模型思考**：**运行时守卫注入的决定**。2026-10-12 那次任务里连出现三次的
  "Approved visual approach: one bounded step…" 和 "Read depth data before deciding…"
  **全是守卫自己写的字符串**——模型那一轮选的动作被守卫覆盖掉了，而 UI 把它们当成了
  "模型思考"。所以你看到"模型一直在说要抵近"，其实模型根本没这么想。

已修：守卫注入的决定现在带 `source="guard"`，UI 单独显示为 **🛡 运行时守卫**（不再混进模型思考）。

**② 为什么"点不开看详细内容"？** 思考行的展开器判据是"文本 > 90 字或有换行"，而预览是 CSS 单行省略——面板窄时 40~90 字的文本已经显示成"…"却判定为"不用展开"，操作员**永远看不到全文**。已修：判据改成"现在显示的是不是全部"（>40 字或预览被截断就给出展开器）。

**③ 这次只用了一个 skill 吗？多 skill 怎么办？** 是，只用了 `skill:visual-approach`（时间线里可见）。
技能**目前不能组合**——激活技能只是把它的 Markdown 作为一次观察取回来，运行时没有"当前生效技能"的概念，一个技能也无法调用另一个。真要组合只能靠模型在换阶段时显式激活下一个。见 §3.3。

## 1. 工具清单（现场实测 41 个）

按功能分组。**加粗**的是重叠项。

| 组 | 数量 | 工具 |
|---|---|---|
| 飞行/移动 | 19 | **drone_fly_to**、**drone_move_relative**、**drone_fly_velocity**、**drone_fly_path**、**drone_approach_target**、**drone_rotate_to**、drone_release_yaw、drone_takeoff、drone_land、drone_hover、drone_arm、drone_disarm、drone_connect、drone_disconnect、drone_set_mode、**drone_dispatch_takeoff / path / land / return_land** |
| 任务/航线 | 5 | drone_upload_mission、drone_download_mission、drone_start_mission、drone_get_mission_progress、drone_clear_mission |
| 视觉读取 | 4 | airsim_take_photo、airsim_get_depth_map、airsim_get_sensors、airsim_detect_objects |
| 感知控制/分析 | 5 | perception_start、perception_status、perception_stop、inspect_current_frame、formation_command |
| 状态 | 4 | drone_get_status、drone_list_vehicles、drone_get_parameters、drone_get_firmware_info |
| 记忆 | 3 | memory_store、memory_remember、memory_recall |
| Agent | 1 | agent_subtask |

### 1.1 重叠组与处理

| 重叠 | 现状 | 建议 |
|---|---|---|
| **移动 5 选 1**：fly_to / move_relative / fly_velocity / fly_path / approach_target | 卡片各讲各的，**没有"该选哪个"的规则** ← 这才是"混乱"的根源 | 在系统提示里加一条选型规则（见 §2）。工具本身都有存在理由，先不删 |
| **航线两套体系**：`drone_dispatch_*`(4，异步任务队列) 与 `drone_*_mission`(5，MAVLink mission) | 都在表达"沿航点飞" | 二选一：建议仿真/px4_mavlink 统一走 `mission_*`，`dispatch_*` 标注为旧路径。**涉及行为，需你定** |
| **视觉读取 4 个**：perception_status / airsim_detect_objects / inspect_current_frame / airsim_take_photo | 前两个读同一份快照（一个全量、一个按类别过滤） | 保留但标注同源：`airsim_detect_objects` = `perception_status` 的过滤视图 |
| **遥测重叠**：drone_get_status / airsim_get_sensors / drone_get_parameters / drone_get_firmware_info | get_status 已含 GPS/电池/姿态，sensors 又给 IMU/GPS/距离 | `airsim_get_sensors` 归到"状态"组（现在挂在 perception 下，分类误导） |

## 2. 选型规则（缺的就是这一条，建议进系统提示）

```
要点在哪个阶段    选什么
──────────────────────────────────────────────────────────────
目标被检测锁定      drone_approach_target（首选：track_id + 深度校正 + 障碍检查）
要朝画面里某点走    drone_move_relative(forward_m, right_m)   ← 先取深度算好分量
目标局部坐标已知    drone_fly_to(x, y, z)
只调机头朝向        drone_rotate_to(heading_deg)
多航点序列          drone_fly_path(waypoints_json)
持续速度指令        drone_fly_velocity(vx, vy, vz)
只想读数/看画面     perception_status / inspect_current_frame / airsim_get_depth_map
```

配套纪律（已在 skill 里）：**检测优先**；检测锁不住才走 VLM+深度路径；单步有界；每步复核。

## 3. Skills 清单

### 3.1 两套技能体系（这是另一处"混乱"）

| 类型 | 位置 | 清单 | 模型看到什么 |
|---|---|---|---|
| **硬技能**（可执行序列） | `src/agent/skill_registry.py` | `skill:navigation`、`skill:search`、`skill:visual_observe`、`skill:return_home` | 固定步骤序列，直接执行 |
| **指导技能**（Markdown） | `skills/*/SKILL.md` | `skill:flight-sequence`、`skill:formation`、`skill:region-search`、`skill:target-inspection`、`skill:visual-approach` | 完整操作指导文本，模型照着自己调工具 |

**命名建议**：硬技能保持 `skill:<name>`，指导技能改成 `guide:<name>`（或卡片上显著标"指导"）。
现在两者同名空间、且 `/api/skills` 只列出指导技能，**同一件事有两个入口**（见下）。

### 3.2 技能重叠

| 重叠 | 说明 | 建议 |
|---|---|---|
| `skill:search`（硬，固定扇区扫描）vs `skill:region-search`（指导，讲怎么搜） | 同一个意图两条路，行为不同（一个照做固定航向，一个靠模型按指导决策） | 保留指导版（更灵活），把硬版标注为"旧路径"，或把硬版的纪律并入指导版 |
| `skill:visual_observe`（硬：拍照+分析）vs `inspect_current_frame`（工具） | 硬技能内部就是调这些工具，重复一层 | 硬技能保留（它带参数默认值与顺序），但卡片要说明"它只是这几个工具的组合" |
| `skill:target-inspection`（指导）vs `skill:visual-approach`（指导） | 前者假设**有检测锁定**，后者是**没有锁定**时的退化路径 | 已在对方面写明"优先前者"；这是**有意的分工**，不是重复 |

### 3.3 多技能怎么协作（当前不支持，方案如下）

现状：激活 `skill:X` = 把 X 的 Markdown 作为一次观察取回；运行时**不记录**"当前生效技能"，技能之间**不能互相调用**；先激活的指导会一直留在对话上下文里。

短期规则（写进技能卡片 + 系统提示即可，零代码）：

```
一个任务在同一时刻只有一个权威指导。换阶段时显式激活下一个技能，
并在理由里说明"现在按 <新技能> 执行，上一段指导不再适用"。
典型交接：region-search（找）→ target-inspection（有锁定时靠近）→ visual-approach（锁不住时靠近）
```

中期（需改代码）：技能 frontmatter 增加 `next_skills: [...]` 声明交接；运行时记录 `active_skill` 并在卡片上标注，让"哪份指导现在有效"成为可观测状态。

## 4. 本轮改动清单

| 改动 | 文件 | 为什么 |
|---|---|---|
| 守卫不再覆盖模型的有效动作（两处注入加 `needs_guard_action` 门控） | `agent_loop.py` | 这是那次任务失败的**直接原因**：技能教的 `move_relative` 永远执行不到 |
| 检测锁不住时不注入 `drone_approach_target` | `agent_loop.py` | 不注入一个必然失败的调用 |
| 守卫注入的决定标 `source="guard"` + UI 显示为"运行时守卫" | `agent_loop.py`、`agent_bridge.py`、`ui-chat.js` | 操作员要能分清"模型思考"和"运行时守卫" |
| 思考行展开器判据与预览一致 | `ui-chat.js` | 40~90 字的文本显示成"…"却点不开 |
| 深度查询接受归一化坐标 + 多点中值 + 不可信值标记 | `tools/vision.py` | 彩色图像素传进深度图会静默读错；单像素深度会落在物体边缘 |
| 深度读取改走浮点路径 | `tools/vision.py` | `simGetImage(DepthPlanar)` 返回的是 8 位 RGBA 可视化图，那个"距离"从来就是错的 |
| 新增 `skill:visual-approach` 指导技能 | `skills/visual-approach/SKILL.md` | 检测锁不住时的两条腿走路 |

回归测试：`tests/test_agent_loop_isolation.py`（12 条，含守卫 3 条）、`tests/test_depth_query_points.py`（4 条）。全量 782 通过。

## 5. 待你决策的三件

1. **航线两套体系二选一**（dispatch_* vs mission_*）——涉及行为，我不擅自删。
2. **技能命名分流**（`skill:` 硬 / `guide:` 指导）——会改到 manifest、卡片与技能文档，是纯整理动作，可以先做。
3. **选型规则进系统提示**（§2 那张表）——最小改动、直接减少"选错工具"，建议先做这个。
