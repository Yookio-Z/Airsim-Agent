---
name: flight-sequence
display_name: 飞行序列指导
status: guidance
type: guidance
description: 当操作员给出一条把多个单机步骤串起来的紧凑指令时使用本技能——例如"起飞到3米，向前2米拍照看看有什么，然后返航降落"或"检查状态、起飞、前移并拍一张、返航降落"。覆盖有序工作流：检查状态 → 解锁 → 起飞 → 移动 → 拍照 → 视觉分析 → 返航 → 降落，包括任何水平移动前的安全高度规则、对含糊措辞取保守距离、以及仿真环境下的预检恢复。任何包含多个飞行动作的单机任务（起飞/移动/拍照/返航/降落）都用它。
required_capabilities: [flight_control, telemetry]
subtools: [drone_get_status, drone_set_mode, drone_arm, drone_takeoff, drone_move_relative, drone_fly_to, drone_rotate_to, inspect_current_frame, drone_hover, drone_land]
cost: medium
risk: medium
---

# 飞行序列指导

## 目的

执行一个简短、有序的单机任务，而不把这份文档变成可执行的工具。这是操作知识：读它，
然后从 `available_tool_cards` 里挑原生工具。激活本技能只加载指导——它不执行任何飞行
动作。

## 意图 → 工具

| 操作员意图 | 工具 | 关键参数 |
|---|---|---|
| 读取状态 / 连接 / 位置 | `drone_get_status` | `vehicle_name` 留空 = 读整个机队 |
| 恢复失败的预检 | `drone_set_mode` | `mode=LOITER` |
| 解锁电机 | `drone_arm` | — |
| 起飞 | `drone_takeoff` | `altitude`（未指定时默认 3.0） |
| 机体系短距离移动 | `drone_move_relative` | `forward_m` / `right_m` / `up_m`，`velocity` 1.0–1.5 |
| 飞到已知的本地 NED 点 | `drone_fly_to` | `x`、`y`、`z`（NED；`z=-3.0` 即升高 3 m） |
| 把相机转向某朝向 | `drone_rotate_to` | `heading`，单位度 |
| 拍一张静帧（同时留存证据帧） | `inspect_current_frame` | `save_frame=true` |
| 开放式画面提问（"画面里有什么"） | `inspect_current_frame` | `question` |
| 指定目标确认（"有没有红色车"） | `inspect_current_frame` | `question`（直接问"是否有<目标>"） |
| 保持位置 | `drone_hover` | — |
| 降落 | `drone_land` | — |

## 工作流程

```
1. drone_get_status                    # 记下返航用的起点 x/y
2. drone_set_mode(mode=LOITER)         # 仅用于仿真环境下的预检恢复
3. drone_arm
4. drone_takeoff(altitude=3.0)
5. drone_move_relative(...)            # 机体系，保守
6. inspect_current_frame(save_frame=true)  # 拍照并留存证据帧
7. inspect_current_frame                # 开放问题或"是否有<目标>"确认
8. drone_fly_to(x=start_x, y=start_y, z=-3.0)
9. drone_land
10. drone_get_status                   # 汇报最终状态
```

## 操作规则

- 任何飞行动作之前先读 `drone_get_status`。
- **绝不在低于 1.5 m 时下达水平移动。** 如果无人机没在飞或低于这个高度，先起飞
  （默认 3 m）。无法起飞就如实说明移动被安全层拦下，而不是硬试。
- 对含糊的移动措辞（"一点距离"、"稍微"、"a bit"、"扫一下"），水平移动保持 1–2 m、
  速度 1.0–1.5 m/s，然后悬停再拍照。
- 机体系移动用 `drone_move_relative`，已知 NED 点用 `drone_fly_to`。不要手工在两者
  之间换算。
- 只有后端是仿真/SITL 时才尝试预检恢复：`drone_set_mode(mode=LOITER)`、重读状态、
  检查条件就绪后再解锁。真机上要停下并上报。
- 对"返航"：飞回第 1 步记录的起点 `x`/`y`，用安全的空中 `z`。如果起点位置从未读取
  过，读状态并说明限制，而不是猜坐标。
- 对"扫描周围环境"：不要做大幅低空扫掠。在安全高度拍一到两张静帧，或做一个小幅偏航
  调整——且只在操作员明确要求扫描时。
- 降落后读一次状态，汇报 `armed`、`flying`、模式、NED 位置，以及可用时的碰撞状态。

## 汇报

说明序列是否完成；用一句话列出实际执行的链条；汇报最终状态和大致 NED 位置；概述图像
或视觉模型结果；提及任何被跳过的步骤、失败的工具、安全拦截或不确定之处。

## 失败处理

- 飞行控制失败后停止，除非仿真模式下的恢复明确适用。
- 绝不编造遥测、GPS 坐标或图像内容。
- 如果凭遥测无法解出返航起点：已经接近起点就降落；否则悬停并说明。

## 不适用

- 多机或编队任务——用 `formation` 技能。
- 区域搜索、目标确认或跟踪——用 `region-search` 技能。
- 不需要飞行器动作的知识性问题。

## 精确移动、转向与返航

操作员会注意到每一处不准：少走了 0.8 m、"左转 90°"最后停在 60°、"返航"飞到了没人
知道的地方。这些规则消灭猜测。

**坐标系——先弄清"前"是哪个前。** `drone_move_relative(forward_m/right_m/up_m)` 在
**当前朝向的机体系**里移动。转向之后，"前"指向别处：一个"先左转再前进"的计划会先向
左、再沿新朝向走——这通常是操作员想要的——但如果他说"前进 5 m，然后左转前进 3 m"，
第二段并不平行于第一段。需要固定的世界方向时，用 `drone_fly_to`（绝对 NED）代替
相对移动。

**转向——永远用绝对角。** `drone_rotate_to(heading_deg)` 接受绝对朝向（0 = 正北，
顺时针为正）。"左转 90°"绝不能直接发 `90`；先读当前朝向（`drone_get_status` →
`heading_deg`），然后发 `(heading - 90) % 360`（右转/顺时针用 `+`）。在计划的
reasoning 里写出这个算式，让它可被核对。

**距离精度。** 相对移动是带容差的速度指令，每段预计有大约 0.3–1 m 的误差。把长航段
（>10 m）拆成 2–3 段，每段后读 `drone_get_status`，用第二次短移动修正余量。需要精确
航点时，优先 `drone_fly_to`，并用状态回读核实最终位置。

**返航是一个绝对位置任务。** 不要试图用相对航段拼出来——"返航"飞到离家的地方就是
这么来的。起飞前读位置（那就是 home 点），记住它，结束时用
`drone_fly_to(home_x, home_y, home_z)` 在飞行高度飞回去，然后 `drone_land`。
`drone_fly_to` 会自己把机头转向目标，所以不要额外加一次转向；只有任务需要到达时
有特定观察方向，才刻意转向。

**朝向变化会改变相机的视野。** 云台在世界系里保持俯仰和横滚，但**偏航跟随机身**——
任何转向之后画面都朝着另一个方向。操作员关心在看什么时，转向后要重新确认视野
（`inspect_current_frame`），而不是假设之前的构图仍然成立。

**没有回读，绝不报告动作已完成。** 工具返回"指令已发送"不等于飞行器到达了目标：
有序任务里每一步动作之后，读 `drone_get_status`（位置、高度、朝向），与预期值对比，
再继续下一步。

## 前置条件 —— 每个飞行动作都有

计划任何飞行动作之前，先确认飞行器处于能执行它的状态。任务开始时、每次降落之后，
都要读 `drone_get_status`。

| 动作 | 要求 | 不满足时 |
|---|---|---|
| `drone_rotate_to`、`drone_move_relative`、`drone_fly_to`、`drone_fly_path` | **已升空**（已解锁 + 在飞） | 先解锁并起飞——落地的飞机转不了也平移不了，指令只会失败 |
| `drone_takeoff` | **已解锁** | 先 `drone_arm`（且只在操作员的任务需要飞行时） |
| `drone_arm` | 链路正常、无激活的失效保护 | 报告原因，而不是盲目重试 |
| `drone_land` | 已解锁 / 在飞 | 已经落地的飞机不需要降落 |

上一个任务的状态**不是**当前状态：一个以 `drone_land` 结束的任务会让飞机在地面上
处于未解锁状态，下一个转向请求必须先解锁再起飞。失败的动作不要原样重试——读状态，
弄清它*为什么*失败（通常是"未升空"或"未解锁"），修好前置条件，再重试一次。

前置条件无法满足时要如实报告（"当前未解锁/已落地，无法转向；需要先解锁起飞"），
而不是把同一条失败指令重复到循环用尽步数。
