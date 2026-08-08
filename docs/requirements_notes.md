# 产品需求与当前约束

> 当前状态、阶段优先级和验收标准以 [`system_upgrade_plan.md`](system_upgrade_plan.md) 为准。

## 产品定位

系统是一个以 Windows 为操作中心的智能地面站，融合：

- QGroundControl 风格的连接、遥测、地图和 Mission 能力；
- AirSim 仿真；
- PX4 MAVLink 和 PX4 ROS2 链路；
- 面向后续真机的安全、审批和边侧 Agent 扩展。

## 不可破坏的范围

1. 现有 AirSim Agent 仿真路径必须继续可用。
2. PX4 MAVLink 和 PX4 ROS2 不应被新的边侧 Agent 设计替换。
3. ROS2 Gateway 继续作为 ROS/PX4 确定性控制边界。
4. UI、Agent 和 Backend 使用统一的 Ground Station/Mission 数据模型。
5. LLM 不能进入高频飞控环，不能绕过安全层直接控制飞控。

## Agent 需求

地面主 Agent 应理解自然语言、拆分任务、选择 Skill、调用工具并处理异常。后续边侧 Agent 只负责受限的本地感知、ROS 算法调用和局部重规划。两者通过结构化任务和事件协议联动，而不是共享一套不可观测的内部状态。

## 当前开发优先级

```text
现有仿真回归
  -> 三条 Backend 行为一致性
  -> ROS2 Provider 完整化
  -> WSL/Jetson 边侧协议
  -> Jetson 本地模型和多模态
  -> 真机前安全收敛
```
