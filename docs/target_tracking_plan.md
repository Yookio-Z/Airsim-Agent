# 目标追踪 Sim-to-Real 任务分解

> 状态：规划稿（2026-08-26），待评审
> 目标：在 AirSim 仿真中实现可在真机上复现的目标追踪任务（最终形态：无人机对地面运动目标的视觉闭环追踪）
> 关联文档：[`virtual_real_mapping.md`](virtual_real_mapping.md)、[`system_upgrade_plan.md`](system_upgrade_plan.md)

## 现状盘点（2026-08-26 代码级评估后修正）

> **进度标注（2026-08-27）**：感知轴框架已落地，见 [`perception_axis_design.md`](perception_axis_design.md)
> ——感知能力与飞行后端解耦（T4.1 完成）、`perception_status` 工具上线、runtime 生命周期挂接完成、
> 全量测试 407 passed。后续任务从"框架搭建"转入"算法闭环"（T1/T2、感知 SKILL.md、Jetson 形态）。

> **核心结论：现有"目标追踪系统"从未接线，当前运行系统中不存在任何连续追踪能力。**
> 以下三条追踪路径全部来自 initial commit 的脚手架代码，运行时从未启用：
>
> 1. `src/modules/perception_hub.py`（PerceptionHub）——**无任何调用方**。自带一套独立于
>    `autonomy/world_state.py` 的世界模型；每帧重建 TrackedObject，无时序关联；3D 投影每
>    5 帧才做一次且失败静默吞掉；未使用 frame_source.py 抽象（直连 AirSimVideoStream）。
> 2. `src/modules/target_lock.py`（TargetLock）——**无任何调用方**。
> 3. `src/autonomy/skills/tracking_skill.py` + `policy_engine.py`（自主栈）——PolicyEngine
>    从未启动；且**全仓库没有任何代码写入 `world_state.target_state`**，即使启动，
>    TrackingSkill 读到的目标永远 `visible=False`，只会原地悬停。
>
> 实际注册给 LLM 的工具面（manifest 全量核对）：飞行原语（fly_to/fly_velocity/rotate 等）、
> `airsim_detect_objects`（单帧）、`airsim_get_depth_map`、`airsim_take_photo`、
> `airsim_vlm_analyze_image/confirm_target`、编队、provider 工具。
> **没有连续追踪类工具**——LLM 若要追踪只能"检测→飞一步→再检测"手动循环（≈0.5Hz），
> 这正是 README 明确禁止的 LLM 进入高频控制回路。
>
> **实测数据（本机，CPU）**：YOLO-World v2 推理 274ms/帧 @480×360 ≈ 3.6 FPS；
> 首次加载 383s（含 ultralytics 依赖自检，需用 `YOLO(...)` 前置环境固定或离线跳过修复）。
> 检测质量因磁盘上无带目标的抓拍帧，待 AirSim 启动后补测。

### 值得保留的积木（质量尚可，不推倒重来）

| 积木 | 位置 | 评估 |
|---|---|---|
| 模型加载/类别词表/别名/仿真误检过滤 | `src/modules/yolo_detection.py` | 质量不错，直接复用 |
| 深度→3D 投影 | `occupancy_map.DepthProjection` | 中位数采样+针孔模型基本正确；**两个缺陷**：①角度用线性 FOV 插值而非 atan/tan（90° FOV 边缘误差大）；②忽略 pitch/roll 只做 yaw 旋转——吊舱带云台角时必须补全旋转 |
| 帧源抽象 | `src/modules/frame_source.py` | AirSim/RTSP/USB 三实现已就绪（PerceptionHub 没用它，新链路要用） |
| 数据模型 | `src/autonomy/world_state.py` | `TargetState.track_id/estimated_velocity/lost_time` 已预留，等接线 |
| 飞行原语 + 安全层 + 审批门 | `src/tools/`、`src/autonomy/safety_arbiter.py`、backends | 正常在用 |

### 自研 vs 开源结论

**自研轻量时序跟踪器（IoU 关联 + 恒速 Kalman，~200 行），不引入大型 MOT 框架。**
理由：本任务是单/少目标 + 有深度先验，ByteTrack 等开源方案解决的是密集多目标 ID
切换问题，与我们的痛点（单目标平滑、防抖、短遮挡续跟）不匹配；如后续误检严重，
可选用 roboflow `supervision` 的 ByteTrack 封装做对比，接口上预留即可。

**四个核心差距**（按 2026-08-26 评估修订）：
1. **追踪链路从未接线**：感知→跟踪→控制整条链路需要新建（T1/T2 的实质是"新建并接线"，不是"改造死代码"；PerceptionHub/TargetLock 建议删除或仅作参考）。
2. **感知能力与飞行后端耦合**：`target_tracking` capability 在 backends.py 中按后端硬编码（仅 airsim=True），而真机的正确形态是"图像流来自独立通道 + 速度指令走 MAVLink/ROS2"，两者必须解耦。
3. **3D 定位绑死 AirSim 深度 + 投影缺 pitch/roll**：真机为吊舱云台相机，投影必须支持云台角度（相机外参旋转），单目地面交会作为无深度兜底。
4. **控制回路不适配 OFFBOARD**：无固定频率 setpoint 流；无 yaw/视场保持。

---

## 阶段 T0：指标与场景基线（先行，其余一切工作的验收依据）

### 任务 T0.1 追踪指标记录器

**目标**：让"跟得好不好"变成可量化的数字，所有后续阶段的验收都基于它。

**具体改动**：
1. 新建 `src/autonomy/tracking_metrics.py`：滑动窗口统计——
   - 锁定时间占比（target visible 时间 / 任务时间）
   - 水平距离误差均值/P95（drone↔target 保持距离 vs 设定 engagement_distance）
   - 视场保持率（目标中心落在画面中心区域的时间占比）
   - 遮挡/丢失后重捕获时长
   - 端到端延迟（检测时刻 → 指令下发时刻）
2. 指标随 tick 写入现有 run 日志（`.airsim_agent/runs/*.jsonl` 体系），支持事后离线统计脚本。
3. `scripts/tracking_report.py`：读 jsonl 输出一次任务的指标报告。

**完成标准**：跑一次手动追踪任务能产出上述 5 项指标的数值报告。

**涉及文件**：`src/autonomy/tracking_metrics.py`（新）、`scripts/tracking_report.py`（新）、`src/autonomy/skills/tracking_skill.py`

**依赖**：无

### 任务 T0.2 标准测试场景

**目标**：可重复的目标航路场景，消除"每次手玩"的不可比性。

**具体改动**：
1. `scripts/tracking_scenario.py`：目标载具（AirSim 侧用第二辆车/NPC）沿预设航路移动，三档剧本：
   - S1 静止目标（联调用）
   - S2 步行速度直线往返 ≈1.5 m/s
   - S3 车速环形航路 ≈8 m/s
2. 剧本参数（速度、路径、起始位姿）命令行可调；一键启动/停止并打标签写入 run 日志。

**完成标准**：S1–S3 各能一键复现，日志中可区分剧本标签。

**涉及文件**：`scripts/tracking_scenario.py`（新）、`config/`（场景参数）

**依赖**：无

---

## 阶段 T1：时序跟踪层（检测 → 跟踪）

### 任务 T1.1 多目标时序跟踪器

**目标**：把逐帧检测结果变成平滑、带 ID、带速度估计的目标轨迹。

**具体改动**：
1. 新建 `src/modules/target_tracker.py`：
   - IoU 关联 + 恒速模型 Kalman 滤波（图像平面 bbox 状态为主，NED 位置由投影结果二次平滑）；
   - 输出：主目标的平滑 `estimated_position`、`estimated_velocity`（NED）、`track_id`、coast 预测；
   - 短时遮挡（默认 < 2 s）内维持 coast，超时才置 `visible=False`。
2. 自研轻量实现（预计 ~200 行，依赖仅 numpy），不引入 heavy MOT 框架；若后续误检严重再评估 ByteTrack。

**完成标准**：
- 单元测试：给定抖动 bbox 序列，输出位置平滑、速度估计收敛到真值 ±20%；
- S1/S2 场景下指标报告的距离误差 P95 明显优于无滤波基线。

**涉及文件**：`src/modules/target_tracker.py`（新）、`tests/test_target_tracker.py`（新）

**依赖**：T0.1（用指标证明有效）

### 任务 T1.2 PerceptionHub 接入跟踪器

**目标**：`WorldState.target_state` 由跟踪器（而非裸检测）驱动。

**具体改动**：
1. `_vision_loop` 中检测后插入 tracker.update()；
2. 填充 `TargetState.track_id / estimated_velocity / lost_time / position_history`（字段已预留）；
3. `best_confidence` 改为取跟踪轨迹的平滑置信度。

**完成标准**：S2 场景下 `world_state.target_state` 出现连续 track_id 和非零 estimated_velocity；tracking_skill 日志不再逐 tick 抖动。

**涉及文件**：`src/modules/perception_hub.py`

**依赖**：T1.1

### 任务 T1.3 TrackingSkill 使用速度预测续跟

**目标**：短暂丢失期间按预测轨迹继续追踪而不是立即悬停。

**具体改动**：
1. `on_tick` 丢失分支改用 `predict_position(dt)` + `estimated_velocity` 外推（代码已预留，接上即可）；
2. 给外推加上限幅（预测点距最后实测位置 ≤ N 米），防发散；
3. give-up 阈值统一读 `lost_time` 字段。

**完成标准**：S2 场景人为遮挡 1–2 s 后无需重搜即恢复锁定；重捕获时长指标 < 3 s。

**涉及文件**：`src/autonomy/skills/tracking_skill.py`

**依赖**：T1.2

---

## 阶段 T2：视场保持与控制回路统一

### 任务 T2.1 合并两条控制路径

**目标**：追踪的速度指令只有一个来源，消除 VisionControlPolicy 与 TrackingSkill 并行发令的隐患。

**具体改动**：
1. 将 `VisionControlPolicy.compute_control` 的视场保持逻辑并入 `TrackingSkill`（或抽出共同底层 `TrackingController`）；
2. `PerceptionHub.get_control_command` / `execute_control` 降级为薄兼容代理或删除；
3. 全局检索确认没有其他调用方同时使用两条路径。

**完成标准**：任一 tick 只有一路速度指令下发（run 日志可证）；S2 指标不回退。

**涉及文件**：`src/modules/perception_hub.py`、`src/autonomy/skills/tracking_skill.py`

**依赖**：T1.2

### 任务 T2.2 yaw / 云台视场伺服

**目标**：追踪中目标始终保持在画面内（当前 `_velocity_toward` 完全不管朝向）。

**具体改动**：
1. `TrackingSkill` 增加方位保持环：目标像素误差 → yaw 角速率（P 控制即可）；
2. `airsim_controller` 确认/补充 yaw 速率控制与非阻塞接口；
3. 云台俯仰按目标距离/高度差自适应（若仿真机型有云台）。

**完成标准**：S3 场景下视场保持率 ≥ 90%。

**涉及文件**：`src/autonomy/skills/tracking_skill.py`、`src/modules/airsim_controller.py`

**依赖**：T2.1

### 任务 T2.3 控制环固定频率化

**目标**：为 PX4 OFFBOARD 的 setpoint 流约束（>2 Hz，实际建议 10–20 Hz）做准备，仿真与真机共用同一节拍。

**具体改动**：
1. `TrackingSkill._move_velocity` 从阻塞式 `move_by_velocity(duration=0.4)` 改为独立 15 Hz 定时线程发送非阻塞速度 setpoint；
2. skill on_tick 只负责计算期望速度写入共享槽位（可复用 `src/agent/command_slots.py` 的机制）；
3. 断流保护：setpoint 线程异常停止时自动 hover。

**完成标准**：实测 setpoint 间隔抖动 < 50 ms；追踪指标不劣化。

**涉及文件**：`src/autonomy/skills/tracking_skill.py`、`src/modules/flight_controller.py`（如需接口扩展）

**依赖**：T2.1

---

## 阶段 T3：域差注入与鲁棒化（让仿真比真机难）

### 任务 T3.1 感知退化注入器

**目标**：在仿真里主动制造真机水平的感知质量，提前暴露问题。

**具体改动**：
1. 新建 `src/modules/perception_degrader.py`（挂在 FrameSource 与检测之间）：bbox 抖动（±N px 高斯）、随机误检、概率漏检、深度高斯噪声（σ 按 % 距离）、整体帧延迟（100–300 ms）；
2. 全部参数走 config，默认关闭。

**完成标准**：开启"中等退化"配置下 S2 场景指标仍达标（阈值在 T3.2 定）。

**涉及文件**：`src/modules/perception_degrader.py`（新）、`config/`

**依赖**：T0.2、T1.2

### 任务 T3.2 参数整定

**目标**：按指标报告系统性整定，而非手感调参。

**具体改动**：
1. 整定对象：Kalman Q/R、coast 时长、丢失 give-up 阈值、视场伺服增益、限速/限加速度；
2. 每组参数跑 S1–S3 × 无退化/中等退化矩阵，产出对比表存 `docs/` 或 logs。

**完成标准**：选定一组通过全部场景的参数并固化为默认值。

**涉及文件**：`config/`、各模块参数读取点

**依赖**：T0.1、T3.1、T2.x

### 任务 T3.3 追踪安全包络（仿真侧先落地）

**目标**：追踪模式的硬安全边界，真机审批门的实质内容。

**具体改动**：
1. safety_arbiter 增加追踪专用规则：最大追踪速度、距起飞点最大半径（软围栏，越界自动减速返航）、最低离地高度；
2. TrackingSkill 指令超时（如 0.5 s 未成功下发）自动 hover；
3. 与 policy_engine 的 STOP_TRACKING 升级路径打通（低电量、GPS 异常时强制退出追踪）。

**完成标准**：人为构造越界/低电量场景，安全动作全部正确触发（pytest 用例）。

**涉及文件**：`src/autonomy/safety_arbiter.py`、`src/autonomy/policy_engine.py`、`src/autonomy/skills/tracking_skill.py`、`tests/`

**依赖**：T2.x

---

## 阶段 T4：感知/后端解耦与双端验证（sim-to-real 的结构关键）

### 任务 T4.1 能力解耦：追踪工具跟随帧源而非后端

**目标**：`target_tracking` 能力由"是否存在可用 FrameSource + 感知模块"推导，与飞行后端正交。这是真机上能跑追踪的结构前提。

**具体改动**：
1. `src/agent/backends.py`：`BackendCapabilities.target_tracking` 不再静态硬编码，运行时按帧源状态计算；
2. 工具注册（`src/tools/perception.py` 等）的门控条件同步修改；
3. Agent loop `_sanitize_decision` 门控同步；
4. 更新能力矩阵文档（virtual_real_mapping.md §2）。

**完成标准**：px4_mavlink 后端 + RTSP 帧源组合下，tracking 工具可见且可执行；纯 px4_mavlink 无帧源时工具不可见（契约测试覆盖两种组合）。

**涉及文件**：`src/agent/backends.py`、`src/tools/perception.py`、`src/agent/agent_loop.py`、`tests/test_tool_manifest*.py`

**依赖**：无（可与 T1 并行启动）

### 任务 T4.2 TargetLocator 定位接口抽象

**目标**：3D 定位方式可替换，仿真换真机只换实现类。

**具体改动**：
1. 新建 `src/modules/target_locator.py`，协议：`locate(bbox, frame_meta) -> {position_ned, depth_m, method}`；
2. 实现：
   - `DepthLocator`：bbox + 深度图反投影（现 perception_hub 逻辑迁出）；
   - `GroundPlaneLocator`：单目假设目标贴地，用无人机高度 + 相机内外参求地面交点（z 取气压计/测距仪）；
3. 配置项选择实现；perception_hub 改为消费 locator 结果。

**完成标准**：GroundPlaneLocator 与 DepthLocator 在 AirSim 中同场景对比，水平位置偏差 < 0.5 m（@10 m 距离）；切换配置无需改代码。

**涉及文件**：`src/modules/target_locator.py`（新）、`src/modules/perception_hub.py`、`config/`

**依赖**：T1.2

### 任务 T4.3 RTSP/USB 相机全链路（桌面级真机预演）

**目标**：不上飞机就能验证真机形态的全链路——外部帧源 + 单目定位 + 跟踪决策。

**具体改动**：
1. `CameraFrameSource`（USB 摄像头）/ `RtspFrameSource` 接入 PerceptionHub（替代 AirSimFrameSource）；
2. 桌面场景：笔记本摄像头对着窗口/走廊，追踪走动的人（悬停在原地只转 yaw，或干脆不起飞只验证感知+决策输出）；
3. GroundPlaneLocator 在此场景实战检验（摄像头高度已知）。

**完成标准**：USB 摄像头下对走动人员持续输出稳定的 estimated_position/velocity 与合理的速度指令序列（不执行也记录）。

**涉及文件**：`src/modules/perception_hub.py`、`config/`

**依赖**：T4.1、T4.2

### 任务 T4.4 SITL 双端冒烟

**目标**：补 virtual_real_mapping.md 已知缺口——同一指令集在 airsim 与 PX4 SITL 双端等效执行。

**具体改动**：
1. `scripts/smoke_dual_backend.py`：同一序列（arm/takeoff/move/hold/land）分别对 airsim 与 px4_mavlink(SITL) 执行并比对遥测响应；
2. 追踪指令在 SITL 下的通路验证（感知来自 T4.3 帧源，速度 setpoint 走 MAVLink）。

**完成标准**：双端冒烟脚本绿；追踪 setpoint 在 SITL OFFBOARD 下被稳定接收。

**涉及文件**：`scripts/smoke_dual_backend.py`（新）、可能微调 `mavlink_controller.py`

**依赖**：T2.3、T4.1

---

## 阶段 T5：真机就绪（部分内容需提前决策，见下节）

### 任务 T5.1 机载架构定型

**目标**：确定感知算力在哪一侧，决定带宽/延迟预算。

**候选方案**：
- A. Jetson 机载跑感知 + 边侧 Agent（system_upgrade_plan Phase 4 路线）：延迟最低，图传只回状态；
- B. 图传 RTSP 回地面站，感知在工作站跑：部署最简单（现 FrameSource 直接支持），受链路延迟约束。

**完成标准**：写出选型结论与延迟预算表（采集→检测→指令→执行的端到端），追加到本文档。

**依赖**：硬件到位情况（需用户输入）

### 任务 T5.2 真机安全清单与首飞 SOP

**具体改动**：
1. PX4 侧：failsafe 参数（低电量/RC 失联/GPS 失联/数据链失联）、硬件围栏；
2. 地面站侧：审批门实测、E-stop 流程演练、追踪模式限速 2 m/s 起步；
3. 首飞 SOP：空旷场地、行人目标、观察员到位、逐级放大 engagement 半径。

**完成标准**：SOP 文档 + 地面演练全部通过。

**依赖**：T3.3、T5.1

### 任务 T5.3 首飞与迭代

**完成标准**：完成一次完整"起飞→搜索→锁定→持续追踪→退出→降落"，指标报告中锁定占比与安全性满足预定门槛。

**依赖**：T5.2、T4.x 全部

---

## 已决策 / 待决策事项

**已决策（2026-08-26）**：
1. **目标类型**：仿真中目标必须能移动（人/车均可）；真机初期不追人（安全风险），改用车辆类目标。
2. **真机传感器**：吊舱云台相机（单目）——投影必须支持云台角（任务 T4.2 中"完整旋转支持"从可选升级为必需）。
3. **算力路线**：图传回地面站与 Jetson 机载两条路都保留。架构上由 FrameSource 抽象天然支持；
   实施顺序建议先图传+地面站（部署最简，RTSP 已支持），Jetson 作为后续演进（Phase 4）。

**仍待决策**：
1. 目标是否配合（按航路走 vs 自由运动），影响 T0.2 场景设计。
2. 吊舱的具体参数（分辨率/FOV/云台控制接口是否开放）——决定 T4.2 外参标定方式与 T2.2 视场伺服的实现深度。

## 依赖关系总览

```text
T0.1 指标 ──┬── T1.1 跟踪器 ── T1.2 接入 ──┬── T1.3 预测续跟
T0.2 场景 ──┘                             ├── T2.1 合并控制 ── T2.2 视场伺服
                                          │                 └─ T2.3 固定频率
                                          ├── T3.1 退化注入 ── T3.2 整定 ── (T3.3 安全并行)
T4.1 能力解耦（可并行起步）─┬─ T4.3 RTSP/USB 全链路
T4.2 Locator 抽象 ─────────┘        └─(与 T2.3)─ T4.4 SITL 双端 ── T5.x 真机
```

## 建议起点

**T0.1 + T1.1 + T4.1** 三件可并行开工：指标是验收地基，T1.1 是技术核心，T4.1 是结构关键且不依赖感知改造。T4.1 完成后即使感知还在仿真阶段，"真机形态"的架构就已经锁定了。
