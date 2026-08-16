---
name: formation
display_name: Formation & Coverage Guidance
status: guidance
type: guidance
description: Use this Markdown skill to plan multi-vehicle formation flight and area coverage missions with the formation_command tool.
required_capabilities: [flight_control]
subtools: [formation_command, drone_list_vehicles, drone_get_status]
cost: medium
risk: high
---

# Formation & Coverage Guidance

## Purpose

Guide the LLM through multi-vehicle formation flight and area coverage missions on the
AirSim and PX4 MAVLink backends. This skill is read as guidance — do not call `skill:formation`.
Use only tools that appear in `available_tool_cards` (formation_command is only
available on the airsim / px4_mavlink backends with at least 2 vehicles).

## When to Use

- the operator asks for formation flight, swarm movement, or a specific formation shape
  ("菱形编队飞到 (50,50)", "三角队形", "编队巡航")
- the operator asks to cover or scan an area with multiple drones ("覆盖扫描 100x50 区域",
  "三机分区搜索整个场地")

## Workflow

```
1. 确认机群:  drone_list_vehicles → formation_command(action=set_drones, vehicle_ids="drone_0,drone_1,...")
2. 设置队形:  formation_command(action=set_formation, formation_type=<line|v_shape|triangle|diamond|square|hexagon|circle|arrow>, spacing=<5.0>)
3. 起飞:      formation_command(action=takeoff, altitude=<10.0>)
              → 返回 mode=formation 后，10Hz 控制环开始维持队形
4. 移动/变形: formation_command(action=move_center, x=, y=) 整队平移
              formation_command(action=rotate, angle_deg=) / (action=scale, scale_factor=)
              → 每步之间用 action=status 确认
5. 覆盖任务:  formation_command(action=coverage_plan, area_shape=, area_width=, area_height=,
              resolution=, partition=, path_algo=) → 查看 total_waypoints
              formation_command(action=coverage_start) → 轮询 action=status 的 progress.percent
6. 收敛确认:  formation_command(action=status) 直到 stable=true（队形）或 percent=100（覆盖）
7. 收尾:      formation_command(action=land_all)（或 hover_all 暂停）
```

## Core Rules

- **一次一个意图**：每轮只发一个 formation_command action；不要连续 takeoff+move_center 在同一轮。
- **步骤间验证**：move/rotate/scale 后先 status 确认再继续。
- **不要混用单机工具**：编队激活期间 drone_takeoff/drone_fly_to/drone_move_relative 等会被
  安全层拦截（返回 BLOCKED）。想停编队：formation_command(action=hover_all) 或 land_all。
- **完成判据**：任务 goal 可声明 {"metric": "formation_stable"}；runtime 会校验
  status 返回的 stable=true 才接受完成。
- **失败处理**：takeoff/land_all 返回 failed 列表时，对 failed 的机单独重试一次；
  连续失败则 stop 并上报。status 里 events 字段会显示 auto_stop/shutdown 原因。
- **紧急情况**：急停会触发全机悬停（无需你操作）；任务结束时若编队仍在飞，runtime
  会自动 hover_all 并停止控制环。
- **安全边界**：编队间距小于 2m 会被拒绝（min spacing）；覆盖区域宽高/半径上限 500m；
  速度上限由安全层钳制。

## Not For

- 单机任务（用 drone_* 工具）
- PX4 ROS2 后端（formation_command 未注册；HTTP bridge 速度语义待验证）
- 需要机间避碰的真实场景（当前为 AirSim 仿真 / PX4 SITL 设计；真机编队需额外研究）
