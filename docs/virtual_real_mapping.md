# 虚实映射与虚控实（Virtual ↔ Real Mapping）

> 状态：架构已就绪（同一执行面 + 后端切换）；本文档是能力矩阵与切换指南
> 更新日期：2026-08-13

## 1. 核心概念

本系统"虚"与"实"共用**同一套地面站、同一套 Agent、同一套工具契约**：

```
                      ┌─────────────────────────────┐
                      │  UI / Agent / 工具层（不变）   │
                      │  FlightController 抽象接口    │
                      └──────────────┬──────────────┘
            虚（仿真）               │               实（真机）
┌──────────────────────┐  ┌─────────┴─────────┐  ┌──────────────────────┐
│ AirSim RPC           │  │ PX4 MAVLink       │  │ PX4 ROS2 Gateway     │
│ (多机、相机、深度)     │  │ (UDP/TCP/串口)     │  │ (HTTP→ROS2→PX4)      │
└──────────────────────┘  └───────────────────┘  └──────────────────────┘
```

- **虚控实**：仿真里训练/验证过的指令（起飞、航线、搜索），切换后端后
  直接控制真机。切换 = `set_backend` + 重连，工具集按后端能力自动裁剪。
- **虚实映射**：同一指令在各后端的等效执行方式（见 §3 映射表）。

## 2. 三后端能力矩阵（src/agent/backends.py）

| 能力 | airsim | px4_mavlink | px4_ros2 |
|---|---|---|---|
| flight_control / telemetry | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ |
| mode_control | ❌ | ✅ | ✅ |
| gps | ❌ | ✅ | ✅ |
| image_capture / depth / detection | ✅ / ✅ / ✅ | ❌ / ❌ / ❌ | ❌ / ✅ / ❌ |
| target_search / target_tracking | ✅ / ✅ | ❌ / ❌ | ❌ / ❌ |
| obstacle_avoidance | ✅ | ❌ | ✅ |
| multi_vehicle | ✅ | ❌ | ❌ |
| ros2_topics | ❌ | ❌ | ✅ |
| real_vehicle（审批门） | ❌ | 串口强制 ✅ | 视配置 |

关键设计：**能力差异由 capabilities 自动裁剪工具集**——切到真机后端时，
AirSim 专属工具（相机/深度/搜索）自动消失，LLM 不会尝试调用不存在的工具
（工具注册有方法门控 + Agent loop 有 `_sanitize_decision` 双保险）。

## 3. 控制指令映射表（虚 ↔ 实）

| 抽象指令 | AirSim RPC | PX4 MAVLink | PX4 ROS2 Gateway |
|---|---|---|---|
| 连接 | `connect(ip,port)` | `connect(url)` | `connect(url=:8766)` |
| 解锁 | `arm()` | `MAV_CMD_COMPONENT_ARM_DISARM` | `/px4/arm` |
| 起飞 | `takeoffAsync` | `MAV_CMD_NAV_TAKEOFF`（OFFBOARD 兜底） | `/px4/takeoff` |
| 降落 | `landAsync` | `MAV_CMD_NAV_LAND` | `/px4/land` |
| 悬停 | `hoverAsync` | OFFBOARD 0 速保持 | `/px4/hold` |
| 飞到 NED 点 | `moveToPositionAsync` | `SET_POSITION_TARGET_LOCAL_NED` | `/px4/setpoint/local_ned` |
| 速度指令 | `moveByVelocityAsync` | `SET_POSITION_TARGET_LOCAL_NED`（速度掩码） | `/px4/setpoint` velocity |
| 航点航线 | `moveOnPathAsync` | mission 上传 / setpoint 序列 | `/px4/path` |
| 转向 | `rotateToYawAsync` | OFFBOARD yaw-rate setpoint | `/px4/rotate_to` |
| 模式 | 无 | `MAV_CMD_DO_SET_MODE`（AUTO/GUIDED/OFFBOARD/RTL） | `/px4/set_mode` |
| 航点任务 | 支持 | `MISSION_COUNT/ITEM` 上传+启动 | 无 mission，任务队列替代 |
| 遥测 | RPC 状态 | 20 种 MAVLink 消息 | SSE `/providers/px4/telemetry/stream` |
| 地理围栏 | 无 | 无（后续项） | `/providers/safety/geofence` |
| 多机 | ✅ vehicle_name | ❌ 单链路单机 | ❌ 单机 |

## 4. 虚控实切换流程

```
仿真（airsim）训练完成
  → 保持同一套 SKILL/指令/工具序列
  → UI Links 面板切到 px4_ros2（或 MAVLink 串口）
  → set_backend：换控制器 + 重连 + 工具集自动裁剪
  → capabilities 变化自动生效：
      * 相机/深度/搜索工具消失（后端无此能力）
      * 真机后端 requires_operator_approval → 高风险动作（arm/起飞/任务）
        自动走操作员审批门（审批信息含目标载具）
      * gps/mode_control 工具出现（MAVLink/ROS2 具备）
  → 同一句"起飞到 5 米"在仿真与真机等效执行
```

## 5. 已验证 / 差距

**已验证**：后端切换全链路（`set_backend` → reconnect → 工具重注册）、
能力裁剪、真机审批门（P5）、工具契约一致性（manifest 契约测试）、
多机 vehicle_name 语义（`""`=默认机 / `"all"`=全体 / 名称=单机）。

**差距（后续项）**：
- MAVLink 地理围栏（真机硬围栏应在飞控侧配置；地面站侧为 ROS2 专属）
- 虚实差异测试环境：同一指令集在 airsim 与 SITL 的双端冒烟脚本
- 真机多机：MAVLink 协议单机，需多链路/多实例（规划中）
- Jetson 边侧 Agent（system_upgrade_plan Phase 4）：边侧本地感知 + 有限重规划，
  地面站保持全局规划与审批

## 6. 新后端接入模板

新增后端（如真机 QGC 直连、图传平台）只需：
1. `src/modules/` 实现 `FlightController` 接口（或复用 MavlinkController）
2. `src/agent/backends.py` 注册 `BackendProfile`（capabilities 声明）
3. 工具层自动适配（同一 `register_core_tools`）
4. 能力矩阵表同步更新
