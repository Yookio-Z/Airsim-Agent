# 感知轴设计：飞行链路与感知能力的两轴正交架构

> 状态：**已定稿，进入实施**（2026-08-27）
> 目的：仿真(今天)与真机(未来)共用同一套 Agent/原子工具/SKILL.md；仿真可跑，真机只换配置。
> 原则：Agent 只做高层语义；识别/追踪/深度等确定性算法全部在感知服务内闭环，不进 LLM 循环。

## 1. 核心概念：两个正交的轴

```
轴 A: 飞行链路  controller —— px4_mavlink / airsim / px4_ros2 / 真机(串口或局域网)
                决定:飞行工具面、遥测、模式、任务、审批属性        (已有,保持不动)

轴 B: 感知能力  frame_source + 算法部署位置
                frame_source: airsim相机 / RTSP吊舱 / Jetson本地 / USB
                deploy:       local(地面站进程内) | remote(Jetson HTTP 服务)
                (本次新建)
```

- **轴 A 与轴 B 完全正交、自由组合**。同一个"追踪"任务在四个组合下代码零改动，只换配置。
- Agent 只消费两个轴的**状态**（飞行状态 + 感知健康/目标状态/事件），不关心算法细节和物理位置。
- 感知服务是唯一写入 `world_state.target_state` 的地方（此前全仓库无人写这个字段，追踪 Skill 读到的永远是空——这是本次要解的死结）。

## 2. 组合矩阵（全部场景 = 换配置）

| 场景 | 轴A 飞行 | 轴B 感知 | 配置 |
|---|---|---|---|
| 今天仿真 | px4_mavlink → Jetson SITL（或 airsim） | local + airsim 帧源 | `perception.profile=sim_local` |
| 机载预演 | px4_mavlink → Jetson SITL | remote + Jetson 跑同一代码 | `perception.profile=jetson_remote` |
| 真机·图传回传 | px4_mavlink(真机) | local + RTSP 吊舱流 | `perception.profile=rtsp_local` |
| 真机·机载 | px4_mavlink(真机) | remote + Jetson 本地相机 | `perception.profile=jetson_remote` |

## 3. 配置模型

扩展 `src/config.py`（配置来自 `config/` 目录，gitignore 之外持久化）：

```yaml
perception:
  enabled: false            # 显式开启；关闭时感知轴完全静默，飞行轴行为与现状一致
  profile: sim_local        # sim_local | jetson_remote | rtsp_local
  frame_source: airsim      # airsim | rtsp | usb | none
  deploy: local             # local | remote
  remote_url: ""            # deploy=remote 时: http://<jetson_ip>:<port>
  target_class: car         # 检测目标类别（YOLO-World 开放词汇）
  confidence: 0.25
  update_fps: 5             # 目标状态更新频率上限
  health_timeout_sec: 3.0   # 心跳超时判定感知离线
```

**注册表**：`PerceptionProfile`（dataclass，同 `BackendProfile` 风格）由 `src/modules/perception_profile.py` 提供，支持：
- `load_from_config(config)`：从配置构造
- `to_dict()`：供 UI/Agent 状态展示
- 内置三个命名 profile：`sim_local`、`jetson_remote`、`rtsp_local`（各带合理默认值，用户可覆盖）

## 4. 感知服务接口

`src/modules/perception_service.py`：

```python
class PerceptionService(Protocol):
    def start(self) -> bool: ...          # 启动感知线程/连接；失败返回 False 并记录原因
    def stop(self) -> None: ...
    @property
    def is_online(self) -> bool: ...      # 由健康心跳驱动
    def health(self) -> dict: ...         # {online, fps, latency_ms, last_update_ts, error}
    def snapshot(self) -> dict: ...       # 目标检测快照（LLM 只读工具的数据源）
    def pop_events(self) -> list[dict]: ...  # 感知事件(目标发现/丢失/恢复),供 Agent 消费
```

**两个实现（同一份语义）**：

1. `LocalPerceptionService`（仿真/图传回传形态）：
   - 帧源 = `frame_source.py` 的 `AirSimFrameSource` / `RtspFrameSource` / `CameraFrameSource`（协议已就绪，本次接线）
   - 检测 = `yolo_detection.run_yolo_detection`（复用现有积木，YOLO-World）
   - 3D 投影 = `occupancy_map.DepthProjection`（AirSim 深度）；`ground_plane` locator 留 roadmap
   - 输出 = 写 `world_state.target_state` + 维护自身快照/事件/健康
   - 线程模型：单一感知线程，`update_fps` 节流；检测与写状态分离，绝不阻塞 Agent 主循环
2. `RemotePerceptionService`（Jetson 机载形态，本次为协议壳）：
   - `remote_url` 探活（GET /health），轮询（GET /snapshot），同一 JSON 协议
   - **Jetson 侧将来运行与 Local 相同的算法代码**（同一份 `perception_service` 包，仅换帧源为本地相机），通过轻量 HTTP 服务暴露 `health/snapshot/events`
   - 协议字段在本文档 §7 冻结，Jetson 实现必须兼容

**运行时选路**：`PerceptionHub 生命周期管理器`（新建 `src/modules/perception_hub.py` 的正式版本 —— 注意：现有 `perception_hub.py` 已确认是死代码，本次以新骨架文件 `perception_axis.py` 承载，避免与旧文件冲突；旧文件保持不动，后续删除在单独提交处理）
选路规则：
```
remote_url 配置且可达(探测成功)  -> RemotePerceptionService
否则(enabled 且未配 remote 或探测失败) -> LocalPerceptionService(按 frame_source)
enabled=false -> 不启停感知,轴 B 静默
```

**与 AgentRuntime 的挂接**：runtime 启动时按配置创建感知服务并 `start()`；停止时 `stop()`。感知服务持有的 `WorldState` 引用即 policy/回读用的同一实例——写 `target_state` 即打通全链路。

## 5. 感知工具的门控规则（本次重构的核心）

现状问题：感知工具（`airsim_take_photo/detect_objects/get_depth_map` 等）由**飞行后端 capabilities** 决定注册（`tool_executor.ensure_ready`），导致 px4 后端感知工具永久消失、且感知与飞行强耦合。

新规则（向后兼容，airsim 现状行为不变）：

```
感知工具注册条件 = 现有条件(image_capture or object_detection 等) OR 感知轴 enabled
感知工具执行前置 = 感知服务在线;离线时工具返回明确错误"perception_offline:<原因>"
```

- 纯 airsim 后端：`image_capture=True` → 注册（与现状完全一致，行为不回退）
- px4 后端 + `perception.enabled=true`：感知工具注册，可执行
- px4 后端 + 未启用感知：不注册（与现状一致）

**新增只读工具** `perception_status`：返回感知健康/目标快照摘要/事件（不依赖具体后端，任何后端可用）。门控 = 感知在线。

## 6. 健康与降级

- 感知服务心跳：local 由感知线程更新时间戳；remote 探测 `/health`。`health_timeout_sec` 未更新 → `is_online=False`
- 降级链：感知离线 → `perception_status` 报告 offline + 事件入队列 → 依赖目标的 Skill/Task（追踪类）读到 `target_state.visible=False` 自动悬停（现有 TrackingSkill 逻辑已天然支持）→ 上报操作员
- 恢复：感知服务重连成功 → 状态回 online，事件记录恢复

## 7. remote 协议（冻结，Jetson 实现必须兼容）

```
GET {remote_url}/health  -> 200 {"online":true,"fps":4.8,"latency_ms":180,"last_update_ts":...}
GET {remote_url}/snapshot-> 200 {
    "targets":[{"class":"car","confidence":0.81,"bbox":[...],"center":[...],
                "world_pos":{"x":..,"y":..,"z":..},"depth_m":..,"distance":..}],
    "primary":{...} | null,
    "timestamp":...}
GET {remote_url}/events  -> 200 {"events":[...]}   # 消费式(读取后清空)
```
总线建议复用 `ros_gateway_controller` 的 HTTP 桥模式（同端口编排能力），不发明新框架。

## 8. 测试策略

- `test_perception_axis.py`：
  - Profile 构造/默认值/序列化
  - 无 AirSim 环境下的 Local 服务启动失败路径（`start()` 返回 False 且 health 可查）
  - `world_state.target_state` 写入契约（用桩帧源 + 桩检测器注入，不依赖真模型）
  - 感知离线 → 门控拒绝执行
- 契约测试更新：manifest 契约顺带覆盖感知工具按新规则注册的组合（airsim / px4+感知 / px4 无感知）
- 回归：全量 pytest 必须保持绿色（airsim 后端行为不回退）

## 9. Roadmap（后续步骤，不在本次范围）

1. 追踪闭环：YOLO+ByteTrack → 平滑目标状态 → OFFBOARD 速度流（复用 MavlinkController 现成 offboard hold）
2. 感知 SKILL.md 三份：detect / track_object / patrol_area（guidance 模板沿用 formation/flight_sequence）
3. Jetson 侧感知服务进程化 + HTTP 桥（remote 形态上线，先图像推流后本地相机）
4. 真机 Profile 显式化 + 链路健康状态机 + 地理围栏
5. `perception_hub.py` 旧死代码删除（单独提交）
6. `ground_plane` locator（真机单目兜底）+ 云台角外参旋转

## 10. 验收清单（本次框架完成标准）

- [ ] 文档定稿（本文档）
- [ ] `perception_profile.py` 可配置/可注册/可序列化
- [ ] `perception_axis.py` 生命周期管理器按配置选路 local/remote/禁用
- [ ] Local 服务：帧源→检测→3D→写 `target_state` 全链在仿真可跑（无 AirSim 时优雅失败）
- [ ] 感知工具门控新规则生效，airsim 行为不回退（全量测试绿）
- [ ] `perception_status` 只读工具任何后端可查
- [ ] virtual_real_mapping.md 能力矩阵同步更新