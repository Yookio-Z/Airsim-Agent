---
name: region_search
display_name: Region Search & Track Guidance
status: guidance
type: guidance
description: Use this Markdown skill to run an area search mission for a target (car/person/truck), confirm it with the vision model only when needed, close in on uncertain targets, and switch to tracking once confirmed.
required_capabilities: []
subtools: [perception_status, drone_get_status, drone_fly_to, drone_move_relative, drone_rotate_to, drone_hover, drone_takeoff, drone_land, airsim_detect_objects, airsim_take_photo, inspect_current_frame, airsim_vlm_confirm_target]
cost: high
risk: high
---

# 区域搜索与目标追踪指南

## Purpose

指导 Agent 完成"搜索目标 → 确认目标 → 追踪目标"的完整任务，核心是
**以低成本检测为主、VLM 确认为辅、不确定就抵近观察**，避免把任务拖成
反复调用视觉模型的空转。像素级识别由感知轴底层服务持续完成
（`airsim_detect_objects` / `perception_status` 只读其结果），Agent 负责
决策与飞行编排，不进入高频控制环。

**定高纪律**：机载相机前视 15°，识别/追踪全程保持 **2~3m** 高度
（不低于 1.8m、不高于 3.5m）。飞高会俯视过度、目标变小、检出率骤降，
所以本类任务起飞高度取 3m，不要按"5m/8m"这类通用巡航高度飞。

**不假设目标位置**：每次任务都从"目标可能不在画面里"开始。禁止凭会话
记忆或上一轮任务假定"目标就在无人机正前方"，找不到必须按下面的搜索流程
主动找，找不到就如实上报。

## When to Use

- 操作员下达搜索/查找目标类指令，例如：
  - "搜索前方的蓝色汽车" / "找找附近有没有人" / "发现目标后跟踪它"
- 目标类别关键词：car / vehicle / truck / bus / person / 车 / 人 / 车辆

## 工具分工（重要：VLM 很贵，能不用就不用）

| 工具 | 成本 | 用途 |
|---|---|---|
| `airsim_detect_objects` | 低（结构化） | **首选**：判断画面里有没有目标类别，返回 class/confidence/bbox |
| `perception_status` | 低 | 读取感知轴持续检测快照（health/targets/primary/events） |
| `inspect_current_frame` | **高（VLM）** | 仅在需要语义判断（颜色、型号、是否同一目标）时调用 |
| `airsim_vlm_confirm_target` | **高（VLM）** | 目标确认；与 inspect 二选一，不要连续调用 |
| `airsim_take_photo` | 中 | 留存证据帧；**不要**靠它"看懂"画面（文本循环读不了图像） |

## 决策逻辑（核心）

```
检测到目标(airsim_detect_objects / perception_status.primary)
        │
        ├─ 置信度足够 且 任务不要求语义属性 → 直接进入追踪
        │
        └─ 需要确认属性(颜色/型号) 或 置信度偏低
                │
                ▼
        调用 1 次 VLM 确认（inspect_current_frame）
                │
                ├─ 确认成功 → 进入追踪
                │
                └─ 不确定（距离远/细节不足/结果含糊）
                        │
                        ▼
                 抵近观察：向前/向目标移动一小段（drone_move_relative forward≈3m
                 或 drone_fly_to 靠近 2~3m），保持定高 2~3m
                        │
                        ▼
                 重新检测 → 仍需确认才再调 1 次 VLM
                        （抵近-确认最多 3 轮；仍不确定则如实上报"无法确认"）
```

**关键纪律**：
- 每次抵近后**最多调用 1 次 VLM**；不要在同一位置重复调用 VLM。
- 连续两次 VLM 结果含糊 → 说明距离/角度不够，改为抵近或换角度，而不是再问一遍。
- 已经确认过的目标，后续跟踪阶段**优先用 `airsim_detect_objects` 复检**，
  不要再每步调用 VLM；目标居中是算法自动完成的，与你无关。

## Workflow

```
1. 起飞准备: drone_get_status 确认 connected 且心跳新鲜;若未起飞
             drone_takeoff(altitude=3), 轮询 get_status 直到 flying=true。
             注意:"已起飞"只看 flying=true，不要只看高度读数(可能是旧值)。
             连接失败最多重连 1 次(drone_connect)，仍失败立即上报。
2. 感知自检: perception_status 确认 health.online=true;
             离线则停任务并上报（不要空转）
3. 搜索（找不到就必须主动找，禁止假设目标在正前方）:
   起步默认"目标不一定在视野里"，每一步都要有一次检测来判定：
   a. 原地扫视(第 0 圈): drone_rotate_to 每 30° 停一下(0→30→…→330)，
      每个朝向调用一次 airsim_detect_objects(target_class=...)。
      出现目标 → 进入上面"决策逻辑"；转完一圈没有 → 进入网格搜索。
   b. 网格搜索(圆形扩展, 航向 0° 北起顺时针, 全程定高 2~3m, velocity<=3):
        第一圈 ±8m:  (8,0) (0,8) (-8,0) (0,-8)
        第二圈 ±20m: (20,0) (0,20) (-20,0) (0,-20)
        第三圈 ±40m: (40,0) (0,40) (-40,0) (0,-40)
      每到一个点: drone_fly_to → 等 2~3s 稳定 → airsim_detect_objects。
   c. 目标距离 >25m 时检出率低(480p 目标过小), 飞进 15m 内再判定"未找到"。
   d. 三圈仍无目标 → 如实上报"未找到"，禁止编造"已锁定"。
4. 抵近确认(不确定时): drone_approach_target 向**已居中的**目标做单步(≤3m)
   前向抵近(视觉伺服式)；每次抵近后重新检测,必要时 1 次 VLM;最多 3 轮。
   目标未居中时先用 drone_rotate_to 对准再抵近。不要用未知坐标盲飞。
5. 追踪(确认后): 保持 2~3m 距离跟随
   a0. 目标居中由**算法自动完成**（感知轴锁定后运行时自动做视觉伺服：横向 yaw
       对准 + 纵向升降微调），Agent 无需任何居中动作，只要保持"已锁定/在追踪"即可。
   a. 目标仍有 world_pos → drone_fly_to(目标附近 2~3m 处, z=-2.5~-3.0)
   b. 目标可见但无 world_pos → 悬停 + 复检快照(等 3D 定位)
   c. 目标丢失: 30s 内看到过 → 回最后已知位置附近再查 1 轮;
      超过 30s → 上报"目标丢失, 已停止追踪"
6. 收尾: drone_land 降落; 摘要含 {search_rounds, target_class, color/确认结果,
         found/failed, confidence, 抵近轮数}
```

## 核心规则

- **每次感知检查之间必须有飞行动作或状态确认**，不允许连续空转查询。
- **不假设目标位置(关键)**：禁止凭会话记忆/上一轮任务假定目标在正前方；
  找不到就按第 3 步"原地扫视 → 网格搜索"主动找，找不到如实上报。
- **VLM 使用预算**：一个任务里 VLM 调用总数尽量 ≤3 次；每次必须带来新的
  信息（换了距离/角度之后），否则不要调用。
- **飞行指令失败处理(关键)**：任何飞行工具失败(arm/takeoff/fly_to/land 返回
  error)时,**最多重试 1 次**;再次失败 → **立即停止任务**,上报
  {stage, tool, error, 建议}。典型原因:仿真链路不可用。
  若连续 2 次感知 health.online=false → 同样停止并上报。
- **链路自检**: 起飞前用 drone_get_status 确认 connected 与心跳新鲜;失败
  最多重连 1 次,仍失败立即上报（运行时会再做一次任务前链路自检）。
- **追踪时位置语义**: world_pos 是 NED 坐标;飞行目标点用其 x/y,z 取
  -2.5~-3.0（定高 2.5~3m）,保持目标在视野内,不要贴脸(<2m)。
- **搜索范围**: 无明确区域时以当前位置 ±15m 为主;单轮移动不超过 8m,
  全程总航程控制在 120m 内。
- **时间卫生**: 整个任务目标 **180 秒内完成**。VLM 单次约 10~20s，抵近确认
  最多 3 轮；超时优先收尾降落。
- **定高与安全**: 识别/追踪全程保持 2~3m（不低于 1.8m、不高于 3.5m）;
  任何异常优先悬停→上报。

## Not For

- 感知服务离线时的搜索（先报障）
- 动态障碍规避（AirSim 场景静态为主，仅保持安全高度）
- 真机降落精度要求（仿真验收后真机需重新标定）
