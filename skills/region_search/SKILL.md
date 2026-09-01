---
name: region_search
display_name: Region Search & Track Guidance
status: guidance
type: guidance
description: Use this Markdown skill to run an area search mission for a target (car/person/truck), consume the perception service for detection, and switch to tracking when the target appears.
required_capabilities: []
subtools: [perception_status, drone_get_status, drone_fly_to, drone_move_relative, drone_hover, drone_takeoff, drone_land, airsim_detect_objects]
cost: high
risk: high
---

# 区域搜索与目标追踪指南

## Purpose

指导 Agent 执行"在指定区域搜索某种物体，找到后追踪它；没找到就移动继续搜索"的
完整任务。所有像素级识别/追踪由**感知轴底层服务**完成（`perception_status` 只读其
结果），Agent 只负责任务决策与飞行编排——绝不自己分析图像、绝不进入高频控制环。

## When to Use

- 操作员下达区域搜索/找目标类指令，例如：
  - "在这片区域搜索一辆车" / "找找无人机附近有没有人" / "搜索目标后追踪它"
- 目标类别关键词：car / vehicle / truck / bus / person / 车 / 人 / 车辆

## 感知状态速查（读 perception_status 返回）

```
health:      {online, fps, error}         感知服务是否在线
snapshot:    {targets:[{class,confidence,bbox,center,world_pos}], primary}
events:      最近事件 target_found / target_lost / target_recovered
```

- `health.online=false` → 感知服务不可用：**不执行搜索**，直接上报并停任务。
- `snapshot.primary` 非空 → 已检测到目标（`world_pos` 可能是空，只有仿真深度时才有 3D 坐标）。

## Workflow

```
1. 起飞准备:   drone_get_status 确认连接;若未起飞 drone_takeoff(altitude=3~5m,
              轮询 get_status 直到 flying=true)
2. 感知确认:   perception_status(include_snapshot=true, include_events=false)
              确认 health.online=true;离线则上报停止。
3. 搜索循环(每次执行"检查→移动→再检查",最多 8 轮):
   a. perception_status 取快照
   b. 若 primary 非空(目标出现):
      - 记录 {class, confidence, world_pos}
      - 进入追踪阶段(见下)
      - 轮次计数器清零
   c. 若 primary 为空:
      - 已有目标位置则缩小搜索圈回看;否则向下一搜索点移动:
        drone_fly_to(下一网格点, 高度保持 5~8m, velocity<=3)
      - 搜索点建议(圆形扩展,按航向 0° 北起顺时针):
        第一圈 ±8m:    (8,0) (0,8) (-8,0) (0,-8)
        第二圈 ±20m:   (20,0) (0,20) (-20,0) (0,-20)
        第三圈 ±40m:   (40,0) (0,40) (-40,0) (0,-40)
        每圈之间回到区域中心校准起点;每轮之间 drone_get_status 确认飞行状态正常
   d. 视角提示:相机前下 45°,机头方向就是视野方向。**每个搜索点必须先转向再判定**:
      到达搜索点后用 drone_rotate_to 旋转扫视(每次转 90°,共 4 个方向:0/90/180/270),
      每转一次调一次 perception_status 判定;目标距离 >25m 时视觉检出率低(480p 画面
      目标过小),飞进 15m 内再判定"未找到"
4. 追踪阶段(目标出现后):
   a. 循环:perception_status 取快照
   b. 目标仍可见且有 world_pos→ drone_fly_to(目标位置附近 3m 处) 接近
   c. 目标可见但无 world_pos → 悬停,重申快照(等待 3D 定位)
   d. 目标丢失(primary 空):
      - 最近 30 秒内看到过 → 回到最后已知位置附近再查 1 轮(目标可能绕回)
      - 超过 30 秒未再出现 → 上报"目标丢失,已停止追踪",结束任务
5. 收尾:      drone_land 降落;无人机状态回写;任务摘要含
              {search_rounds, target_class, found/summary, confidence}
```

## 核心规则

- **每次感知检查之间必须有飞行动作或状态确认**，不允许连续空转查询。
- **飞行指令失败处理(关键)**：
  - 任何飞行工具失败(arm/takeoff/fly_to/land 返回 error)时,**最多重试 1 次**;
  - 再次失败 → **立即停止任务**,上报 {stage, tool, error, 建议}。
    典型原因:仿真链路不可用(px4 未连接/AirSim 退出)——不要空转重试。
  - 若连续 2 次感知快照 health.online=false → 同样停止并上报。
- **追踪时位置语义**:world_pos 是 NED 坐标(世界系);飞行目标点用其 x/y,z 取
  -3.0~-5.0(3~5m 高度),保持目标在视野内,不要贴脸(<2m)。
- **搜索范围**:无明确区域时以当前位置 ±15m 为主;单轮移动不超过 8m,
  全程总航程控制在 120m 内(避免超时)。
- **时间卫生**:整个任务(起飞→搜索→追踪→降落)目标 **180 秒内完成**。
  每轮感知往返 ~5s,6 轮搜索 + 追踪阶段务必紧凑;超时优先收尾降落。
- **安全**:不降落到 2m 以下;任何异常(op 消息/状态异常)优先悬停→上报。

## Not For

- 感知服务离线时的搜索(power 先报障)
- 需要避障的动态障碍规避(AirSim 场景静态为主时仅保持安全高度)
- 真机降落精度要求(仿真验收后真机需重新标定)