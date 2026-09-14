# 技术选型约束清单：AirSim 2023 + UE4.27 + ROS 2 Humble + uXRCE-DDS

> 本文是把已锁定选型（AirSim 2023 / UE 4.27 / ROS 2 Humble / DDS）展开成
> 可执行约束后的产物。目标：在动手改代码之前，把会翻车的地方先列清楚。
> 状态：**待验证项已标注**，未实测前不要当作事实。

---

## 0. 一句话结论

**AirSim 顶替的是 PX4 以下的物理世界，不是 PX4 以上。**
只要我们的代码只依赖 PX4 之上的接口（uXRCE-DDS 话题 / MAVLink），
仿真与真机就是同一套代码。这是本次选型的全部价值所在，也是所有设计决策的准绳。

但有一个硬冲突必须先解决：**AirSim 官方验证过的 PX4 版本（≈v1.12.3）
低于 uXRCE-DDS 的稳定起点（v1.14）**。详见 §3。

---

## 1. 选型锁定表

| 项 | 选定 | 约束来源 |
|---|---|---|
| 仿真器 | AirSim（2023 版源码） | 用户决策 |
| 引擎 | Unreal Engine 4.27 | AirSim v1.8.1 是最后一个官方明确支持 UE4.27 的稳定版 |
| 编译工具链 | **Visual Studio 2019** + Win10 SDK 10.0.18362+ | UE4.27 对编译器敏感，混用 VS2022 会产生难排查的链接错误 |
| ROS 2 | **Humble**（Ubuntu 22.04 Jammy） | LTS 至 2027-05 |
| 飞控 ↔ 机载 | **uXRCE-DDS**（MicroXRCEAgent + `px4_msgs`） | PX4 官方推荐路径 |
| 地面站 OS | Windows | 现有 UI 与 Python runtime |

### 版本对应规则（务必遵守）

- `px4_msgs` 的分支 **绑定 PX4 release line，不绑定 ROS 发行版**：
  PX4 v1.15 → `release/1.15`，v1.16 → `release/1.16`，以此类推。
- 因此 **Humble 上可以构建任意 PX4 release 分支的 px4_msgs**，二者正交。
- 反过来说：**px4_msgs 分支选错 → 话题字段对不上 → 静默收不到数据**。这是最常见的坑。

---

## 2. 拓扑：跨机是既定事实，不要绕

UE4.27 + AirSim 跑 Windows；PX4 SITL + MicroXRCEAgent + ROS 2 跑 Linux。
**这决定了仿真环境天然是跨机的**，与真机（Windows 地面站 + Linux 机载）结构一致——
这是好事，不是妥协。

```
[ Windows 主机 ]                        [ Ubuntu 22.04 / WSL2 ]
UE4.27 + AirSim                         PX4 SITL (make px4_sitl_default none_iris)
      |                                        |
      +--- TCP 4560 (simulator MAVLink) ------>+   AirSim 主动连，LockStep
                                               |
                                        MicroXRCEAgent
                                               |
                                        ROS 2 Humble 节点图
                                        (检测 / VIO / 规划 / 避障)
                                               |
      +--- HTTP 8766 (gateway_node) -----------+
      |
地面站 UI + Agent runtime (本项目)
```

### 端口总表

| 用途 | 协议/端口 | 方向 |
|---|---|---|
| AirSim ↔ PX4 仿真器通道 | **TCP 4560** | AirSim → PX4（PX4 监听） |
| MAVLink GCS 通道 | UDP local 14570 → remote 14550 | PX4 → QGC |
| MAVLink Offboard 通道 | UDP local 14580 → remote 14540 | PX4 ↔ Offboard API |
| MicroXRCEAgent | UDP 8888 / 串口 | PX4 ↔ Agent |
| 本项目 gateway | HTTP 8766 | Windows → Linux |
| 地面站 UI | HTTP 8765 | 浏览器 |

### WSL2 特别注意（若选 WSL2 而非独立机/虚拟机）

- WSL2 有独立 IP，**每次重启可能变化**。
- 需设 `export PX4_SIM_HOST_ADDR=<vEthernet (WSL) 的地址>`（PX4 ≥ v1.12.0-beta1 支持）。
- AirSim `settings.json` 需配 `"LocalHostIp": "<Windows 侧地址>"` 与 `"ControlIp": "remote"`。
- 防火墙放行入站 TCP 4560、UDP 14540。
- **建议：优先用独立 Linux 机或固定 IP 的虚拟机**，避免 WSL2 IP 漂移带来的反复调试。

---

## 3. 硬冲突：AirSim 验证过的 PX4 版本 vs DDS 起点

| 事实 | 值 |
|---|---|
| AirSim / ProjectAirSim 官方声明支持的 PX4 | **v1.12.3**（"其他版本可能可用但不受支持"） |
| 旧 AirSim 文档示例 checkout | v1.11.3 |
| uXRCE-DDS 稳定可用起点 | **PX4 v1.14+**（且要求飞控 flash ≥ 2MB） |

**即：AirSim 的"已验证区"和 DDS 的"可用区"不重叠。**

### 三条出路（按推荐度排序）

**出路 A：直接用 PX4 v1.15/v1.16 试 AirSim SITL（首选）**
`make px4_sitl_default none_iris` 这个目标在现行 PX4 中仍然存在（专为 AirSim/jMAVSim 这类
外部仿真器设计）。HIL_* 系列 simulator MAVLink 消息在新版中未见移除迹象，
**但这个组合无人背书，必须实测**。

**出路 B：HITL 绕开版本冲突（强烈推荐作为验证手段）**
AirSim 用 HITL 模式经 USB 连真实飞控，飞控跑最新固件（v1.15+），
DDS 走 TELEM2 串口（`UXRCE_DDS_CFG = TELEM2`）到 Jetson。
- 好处：彻底解耦 AirSim 版本与 PX4 版本；同时验证的正是真机链路。
- 代价：需要真实飞控硬件；HITL 仅支持 HIL Quadcopter X 机架。
- **这恰好是我们项目要验证的东西**（ROS 2 机载栈 + 地面站），而不是验证飞控固件本身。

**出路 C：退到 PX4 v1.13**
v1.13 已有实验性 uXRCE-DDS。兼容性风险最低，但站在旧版本上，不推荐作为长期基线。

### 硬件前置检查（做不到则 DDS 路线整体不成立）

- 飞控 flash **必须 ≥ 2MB**。Pixhawk 2.4.8 / FMUv2 等 1MB 板**跑不了** uXRCE-DDS。
- 这类板只能走 MAVROS（MAVLink）路线。**选型前先确认飞控型号。**

---

## 4. 仿真 / 真机接缝分析（本项目的核心价值点）

```
        仿真                                     真机
  ┌───────────────┐                       ┌───────────────┐
  │  AirSim+UE4.27│                       │  真实物理世界  │
  │  (虚拟传感器) │                       │  + 真实传感器  │
  └───────┬───────┘                       └───────┬───────┘
          │ TCP 4560                              │ I2C/SPI/UART
          │ simulator MAVLink                     │
  ┌───────▼───────┐                       ┌───────▼───────┐
  │  PX4 SITL     │                       │  PX4 FMU 实飞 │
  └───────┬───────┘                       └───────┬───────┘
          │ uXRCE-DDS                             │ uXRCE-DDS (串口)
          ▼                                       ▼
  ┌───────────────────────────────────────────────────────┐
  │  MicroXRCEAgent + ROS 2 Humble 节点图                  │  ← 完全一致
  │  检测 / VIO / 规划 / 避障 / offboard 控制              │
  └───────────────────────┬───────────────────────────────┘
                          │ HTTP 8766
                  ┌───────▼────────┐
                  │ 本项目地面站   │  ← 完全一致
                  └────────────────┘
```

**结论：接缝在 PX4 这一层。PX4 以上（ROS 2 节点 + 地面站）仿真与真机零差异。**
这意味着：只要我们把所有业务代码写在 PX4 之上，仿真验证就等价于真机验证。

> 反面教材：现有 `src/modules/perception_hub.py:396/456` 直接抱 AirSim 的
> `controller.client` 取流，`:580` 在地面站进程内 `move_by_velocity`。
> 这类代码把逻辑放在了**接缝之下**，仿真里能跑，真机上必然失效。

---

## 5. 由此推出的硬约束（写代码前必须接受）

### 约束 1：高频闭环只能在 ROS 2 侧

地面站（Windows）到 PX4 的链路是：HTTP 8766 → ROS 2 → DDS → PX4。
即便仿真环境也是跨机。**任何 10~30Hz 的闭环都不允许留在 Windows 侧。**

必须下沉到 ROS 2 的部分：
- 视觉伺服 / 目标锁定（`perception_hub.py:580`、`target_lock.py:242`）
- 避障反应（`obstacle_avoidance.py:218` 的深度判据）
- Offboard 设定点维持（gateway 里已有 10Hz tick，方向正确）

地面站只保留：任务编排、航点下发、模式切换、审批门、状态展示。

### 约束 2：图像必须压缩后跨越 Windows↔Linux 边界

仿真时图像源在 Windows（AirSim），但算法节点应在 Linux 侧（与真机一致）。
1080p30 原始 RGB ≈ 180 MB/s，裸传不可行。

处理：AirSim bridge 节点以 `image_transport` 压缩格式（JPEG/H.265）发布，
订阅侧解压。**不因仿真便利而改变算法节点的位置**——否则仿真验证的就不是真机链路。

### 约束 3：Windows 侧不装 ROS

保持现有 `gateway_node.py`（HTTP 8766）作为唯一跨边界通道。理由：
- Windows 上的 ROS 2 支持是实验性的，大量包无 Windows 版本。
- HTTP 是最易调试、最易跨语言的边界，且与语言/框架解耦。
- 现有 `FrameSource` 已支持 RTSP，图像走 RTSP、控制与元数据走 HTTP，分工正确。

### 约束 4：代码不得依赖 AirSim 特有类型

AirSim 已停维护（微软 2022 停开发，2023-12-15 关停）。
Cosys-AirSim 是 API 兼容的 drop-in 替换，是未来的无痛迁移路径。

因此：**所有取流走 `FrameSource`，所有控制走 `FlightController` 抽象**。
禁止在业务代码里出现 `client.simGetImages` / `client.moveByVelocity` 这类调用。
违反此条 = 主动放弃迁移能力。

---

## 6. 分阶段验证清单（按顺序做，每步隔离一类风险）

| 阶段 | 目标 | 通过判据 | 隔离的风险 |
|---|---|---|---|
| **S1** | AirSim(Win) + PX4 SITL(Linux) 连通 | PX4 日志出现 `Simulator connected on TCP port 4560` 且 `EKF commencing GPS fusion` | 版本兼容 + 跨机网络 |
| **S2** | PX4 SITL + MicroXRCEAgent + ROS 2 Humble 出数 | `ros2 topic echo /fmu/out/vehicle_status` 有数据 | px4_msgs 分支匹配 + QoS |
| **S3** | ROS 2 offboard 控制闭环 | 发布 `/fmu/in/trajectory_setpoint` 能驱动 SITL 飞行器 | DDS 链路 + 看门狗 |
| **S4** | gateway HTTP 端点打通 | Windows 侧经 8766 读到状态、下发指令 | 跨边界通道 |
| **S5** | 检测节点接入（先不接我们自己的算法，用社区包跑通） | ROS 2 侧出现 detection topic | 图像桥接 + 算法节点位置 |
| **S6** | 地面站任务编排跑通 | 下发航点任务 → 自主执行 → 状态回传 | 整体 |

**S1 不通过就不要往下走。** 若 S1 失败，先切 HITL（出路 B）绕过。

### QoS 提醒

PX4 以 **best-effort** 发布。订阅端必须显式声明匹配的 QoS，否则**静默收不到任何数据**、且不报错：

```python
from rclpy.qos import QoSProfile, ReliabilityPolicy
qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
```

---

## 7. 待决问题（需要你拍板）

1. **飞控型号？** 决定 uXRCE-DDS 路线是否成立（flash ≥ 2MB 是硬门槛）。
2. **Linux 环境形态？** WSL2 / 独立物理机 / 虚拟机。推荐独立机或固定 IP 虚拟机。
3. **PX4 版本基线？** 建议 v1.15 或 v1.16 起步，但需 S1 实测；失败则切 HITL。
4. **Jetson 型号？** 影响 JetPack 版本 → Ubuntu 版本 → 能否装 Humble
   （JetPack 6 = Ubuntu 22.04 ✓；JetPack 5 = Ubuntu 20.04，需改用 Foxy 或自行评估）。
5. **仿真时检测算法放 Windows 还是 Linux？** 建议放 Linux（见约束 2），
   代价是需要先把图像桥接做出来。

---

## 8. 与现有代码的关系（衔接上一轮诊断）

| 现有资产 | 处置 |
|---|---|
| `frame_source.py` `FrameSource` Protocol | **保留并作为强制入口** |
| `perception_axis.py` Local/Remote Engine + `detect_fn` 注入 | **保留，扩展 ROS 2 provider** |
| `perception_profile.py` 三预设 | 扩展，新增 `ros2_local` / `ros2_remote` |
| `flight_controller.py` `FlightController(ABC)` | 保留；新增 ROS 2 provider 实现 |
| `gateway_node.py` HTTP 8766 | 保留；**补齐 image/detection/depth/planner/tracking 服务端实现** |
| `perception_hub.py` / `target_lock.py` 直接操作 controller | **必须拆，逻辑下沉 ROS 2** |
| `docs/real_vehicle_deployment.md` "ROS 不是必须" | **该结论作废**，需修订 |

---

## 参考来源

- AirSim PX4 SITL 设置（含 WSL2 章节）：`docs/px4_sitl.md`、`docs/px4_sitl_wsl2.md`
- PX4 官方 ROS 2 指南：推荐 ROS 2 + uXRCE-DDS，"Use ROS 2 for new projects"
- `px4_msgs` README：PX4 release → 分支对应表；ROS 2 发行版 → Ubuntu 对应表
- ProjectAirSim：明确支持 PX4 v1.12.3，其他版本不受支持
- AirSim v1.8.1 + UE4.27 + VS2019 工具链要求（社区实测汇总）
