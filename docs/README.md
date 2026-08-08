# 文档导航

本目录描述 AirSim/PX4/ROS2/Agent 地面站系统的架构、运行边界、开发任务和验证方式。

## 权威文档

| 文档 | 作用 | 状态 |
|---|---|---|
| [`system_upgrade_plan.md`](system_upgrade_plan.md) | 当前系统基线、未完成项、仿真优先路线、Jetson 边侧扩展方案 | 当前主线 |
| [`ros_provider_bridge.md`](ros_provider_bridge.md) | PX4 ROS2 Gateway 的运行方式、HTTP API 和 ROS Provider 接入规范 | 当前运行文档 |
| [`agent_harness_design.md`](agent_harness_design.md) | Agent Loop、Skill、Provider、状态、安全和验证原则 | 稳定设计 |
| [`agent_tool_skill_refactor.md`](agent_tool_skill_refactor.md) | 原子工具、Skill 和 ROS Provider 的边界约定 | 稳定设计 |

## 专题文档

| 文档 | 作用 |
|---|---|
| [`agent_architecture.md`](agent_architecture.md) | 智能地面站、Backend、Mission 和 Agent 的总体架构背景 |
| [`qgc_settings_blueprint.md`](qgc_settings_blueprint.md) | QGroundControl 风格设置、车辆配置和参数页面设计 |
| [`project_structure.md`](project_structure.md) | 仓库目录与代码归属说明 |
| [`requirements_notes.md`](requirements_notes.md) | 产品需求、范围和当前约束的简表 |

## 历史和详细任务文档

| 文档 | 说明 |
|---|---|
| [`dev_task_list.md`](dev_task_list.md) | 早期阶段的详细任务拆解。任务状态可能滞后，新的优先级以 `system_upgrade_plan.md` 为准。 |
| [`px4_agent_handoff.md`](px4_agent_handoff.md) | PX4/Agent 开发交接快照，保留验收记录和历史上下文，不作为当前状态的唯一来源。 |

## 文档维护规则

1. 当前系统状态、路线和优先级只在 `system_upgrade_plan.md` 中维护。
2. 运行参数、端口和启动命令写入对应的运行文档，不复制到多个路线文档。
3. 已完成项要有代码、测试或脚本证据；只有设计意图的内容标记为“计划”或“预留”。
4. 不删除历史交接记录；如果内容过时，在文档顶部标明状态并链接到权威文档。
5. 每完成一个阶段，同时更新路线文档、验收脚本说明和相关专题文档。
