# PX4 链路故障报告：arm 被拒与空中悬浮状态(2026-08-28 凌晨)

> 状态：**环境级问题，代码无缺陷，修复需 AirSim/PX4 两侧环境操作**
> 关联：docs/perception_axis_design.md、docs/virtual_real_mapping.md

## 症状现象（实测记录）

| 现象 | 实测 |
|---|---|
| MAVLink 连接/遥测 | ✅ 正常（心跳新生、GPS fix=3、15 颗卫星、位置新鲜） |
| 起飞（首次会话） | ✅ 成功，起飞到约 2.86m（那时 arm 是成功的） |
| fly_to（OFFBOARD 位置指令） | ❌ 多次 33s 到达超时；水平 x/y 偶达、z（高度）不达 |
| 降落+锁定 | ⚠️ 地面站报告 landed_disarmed，**但 AirSim 物理高度悬浮在 4.94m 空中**（速度≈0） |
| 再次 arm | ❌ 全部被拒：`MAV_RESULT_TEMPORARILY_REJECTED (400)`（多余 3 次重试均败） |
| 物理位置 | AirSim 与 PX4 遥测完全一致（3.17, -2.40, z≈4.94），两侧无坐标系偏移 |
| EKF 静止漂移 | 完全 0——位置估计极其稳定 |

### 2026-08-28 执行级新证据（"起飞3m→前飞3m→返航"任务，10 步全 executed）

```
s01 get_status     起点 (-3.84, -0.06, 0)     ← 任务开始前飞机已在此(上一任务残留)
s03 takeoff        → (-3.85, -0.07, -2.85)    起飞成功(真实执行)
s05 move_relative  forward=3m 后位置 → (-0.17, 0.21, -2.52)  ← 前飞3m只动了~0.2m,却报 complete
s07 fly_to         目标 (0.001, -0.0, -3) → "target reached (-3.836, -0.059, -3.0)"
                   ← 飞机往目标反方向飞了3.7m(x负),还报告"到达目标"
s08 land           → (-4.44, -0.11, -2.65) 报 landing complete
s09 get_status     结东位置 (-4.6, -0.12, -1.93)  ← 降落完成后高度仍有~1.9m
```

**归纳**：a) 水平移动指令方向/幅度与期望严重不符（forward 3m ≈ 0.2m；fly_to(0,0) 飞到 -3.8）；b) 触地判定错乱（land complete 但高度 1.9m）。**根因仍指向 PX4 位置估计/坐标语义与 AirSim 物理不一致**——不是 Agent/工具代码缺陷（理解、规划、工具调用、状态回读全部真实执行）。

## 根因分析（证据链）

1. **PX4 认为飞机"在空中"**：HIL 数据显示高度 4.94m；commander 状态机以此为准 →
   对非地面状态的重复 arm 请求返回 `TEMPORARILY_REJECTED`。
2. **物理悬浮不坠落**：disarm 后 PX4 不再给桨指令，但 AirSim 物理继续悬浮在 4.94m、
   速度≈0、且 `simSetVehiclePose` 瞬移会被 PX4 控制立即覆盖回原高度——**PX4 在持续钳制
   这个高度**（PX4 认为的"地面"与 AirSim 场景地面不一致，约 4.9m 偏差）。
3. **最可能的环境根源**：AirSim `settings.json` 中
   - `"ClockType": "SteppableClock"`：PX4 集成场景疑似需要 `ScaledClock`（SteppableClock
     依赖外部时钟步进，PX4 的 lockstep 协议与 AirSim 不互通，`"LockStep": false` 已配置
     但 PX4 侧 `lockstep_scheduler` 仍在初始化——时间同步存在脱节可能）；
   - 垂直基准：AirSim 场景地面（UE z≈3.19）与 PX4 的 HIL 高度基准存在约 4.9m 系统偏差，
     导致触地/降落判定异常（PX4 认为已触地、实际悬浮）。

**判定**：不是 `MavlinkController` / 工具层 / Agent 代码缺陷——同一套代码在 AirSim 后端
（自控车辆）是历史验证可靠的；问题在 PX4↔AirSim 仿真器集成侧。

## 修复方案（白天操作，需要用户在场配合重启环境）

按顺序尝试，每步后验证 `arm → takeoff → fly_to 两段 → land`：

1. **改 AirSim `settings.json`**（`C:\Users\26494\Documents\AirSim\settings.json`）：
   ```
   "ClockType": "ScaledClock",      # 从 SteppableClock 改回，最优先尝试
   ```
   保存后重启 AirSim（UE 场景重新加载，约 3-5 分钟）。
2. **重启 PX4 SITL**（Jetson 手动会话）：
   ```bash
   cd ~/PX4-Autopilot && PX4_SIM_HOSTNAME=192.168.137.1 PX4_SYS_AUTOSTART=10015 ./build/px4_sitl_default/bin/px4
   ```
3. 若仍被拒：在 PX4 控制台执行 `param set MAV_0_BROADCAST 1`，并检查
   `listener vehicle_status` / `listener commander_state` 看状态机是否接地；
   检查 `param show SIM_` 中 lockstep 相关项（`SIM_LOCKSTEP` 若存在置 0 并 `param save`）。
4. 若仍悬浮：把车辆初始高度改成地面以上合理值（settings 中 `"Z": -2` 建议改到场景
   实际地面上方，依据场景地面 UE z≈3.19，可先试 `"Z": 2`），重启 AirSim 再验。

**验证脚本**（Windows，连上 UI 即可）：
`POST /api/gcs/mission/upload + /api/gcs/mission/start` 上传三航点 local_ned 航线
（5,5,-4 / 10,0,-4 / 0,0,-3），观察位置轨迹与高度变化；或直接对话面板发"起飞到3米
然后飞两个点回来降落"。

## 影响范围与临时对策

- px4 链路 **飞行执行** 当前不可用（能遥测、能连接、模式查询正常）。
- **感知链路（识别/追踪算法层）不受影响**——见 perception_axis_design.md，已独立验证。
- 感知轴的 `sim_local`/`rtsp_local`/`jetson_remote` 均不依赖飞行后端状态。
- 修复期间，"区域搜索→识别→追踪" 的 Agent 编排链路可在 dry-run 下验证
  （任务理解、skill 注入、感知工具调用、失败停止与报告），飞行动作待环境修复后即通。