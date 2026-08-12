# 多机 MAVLink / ROS2 支持（QGC 模式）

> 状态：MAVLink 多 system + px4_ros2 多 gateway 已实现（2026-08-13）
> 背景：真实多无人机场景下，PX4 协议本身支持多 system（消息带 sysid/compid），
> QGC 正是靠"单链路多 system 分表"同时管理多架机。

## 1. MAVLink 多机（已实现）

### 架构：单链路多 system 分表

```
一条 UDP 链路（如 14550）上多架 PX4 广播
        │
        ▼
MavlinkController._handle_message
        │ 按 msg.get_srcSystem() 分流
        ▼
_systems = {sysid: {telemetry, position, velocity, gps_origin,
                    last_heartbeat, autopilot, firmware_info, ...}}
        │
        ├─ property 兼容层（_telemetry/_position/... 指向"当前选中机"表）
        │   → 单机路径 ~50 处读写点零改动
        ├─ _resolve_sysids(vehicle_name)：""=默认机 / all=全部 / px4_sysN=单机
        └─ 命令前 _set_target(sysid) 设置 target_system
```

关键设计：
- **选机**：首个心跳触发 `_selected_sysid`；`get_status`/UI 读"选中机"
- **命令**：`arm/takeoff/land/hover/fly_to/fly_velocity/move_relative/fly_path/
  rotate_to_heading/set_mode/upload_mission/download_mission/clear_mission/
  start_mission/get_mission_progress` 全部支持 vehicle_name，逐机执行
  （`""` 绝不隐式广播，与 AirSim 多机语义一致）
- **历史**：`_systems_history[sysid]` 每机独立图表数据
- **单机兼容**：一个 system 时行为与改造前完全一致（141 测试守护）

### 多机连接方式

| 场景 | 方式 |
|---|---|
| PX4 SITL 多实例 | 多个 SITL 实例发往同一 UDP 端口（或同一端口监听），系统按 sysid 自动分机 |
| 真实多机（数传） | 同一数传频段广播，或串口/网络汇聚到地面站一条链路 |
| 需要独立链路 | 每机一条 MAVLink 链路 → 需要"多连接"形态（后续项，见 §3） |

### 测试方法（无硬件）

`tests/test_mavlink_multivehicle.py` 用 fake 消息注入 sysid=1/2 的心跳与位置，
验证：分表、选机、`list_vehicles`、`_resolve_sysids` 语义、命令 target 顺序、
history 隔离、单机兼容。

## 2. px4_ros2 多机（已实现）

每架真机/机载电脑运行一个 gateway（不同端口），地面站按端点管理：

```python
RosGatewayController(endpoints={
    "drone1": "http://127.0.0.1:8766",   # 机 1 gateway
    "drone2": "http://127.0.0.1:8767",   # 机 2 gateway
})
```

- `list_vehicles()` → `["px4_ros2_drone1", "px4_ros2_drone2"]`
- 命令按 vehicle_name 路由到对应端点（`""`=默认端点 / `all`=全部 / 名称=单端点）
- 单端点配置行为不变（`["px4_ros2"]`）
- 测试：`tests/test_ros_multigateway.py`（fake bridge client 记录调用）

## 3. 前端多机（已实现）

- 地图：每机 marker（带机名标签）+ 独立轨迹（多 LineString）
- HUD：载具状态条（机名: 空中/待飞/电量）
- 相机设置：车辆名选择（datalist 建议）
- 航点面板：**载具输入框**（`#missionVehicle`）——航点归属机，上传时
  `MissionPlanDraft.vehicle` 透传到后端（多机时指定目标机上传）

## 4. 后续项

- **多链路形态**（每机独立 MAVLink 连接）：需 ToolRuntime 支持多 controller
  实例，或新增"链路聚合控制器"——真机每机独立数传时的形态
- **编队保持控制循环**（formation）：多机基础已具备（all 同步指令 + 分机状态），
  队形控制器 + 机间防碰撞为独立模块
- **gateway 多机 namespace**（单 gateway 多 namespace）替代多端口方案
