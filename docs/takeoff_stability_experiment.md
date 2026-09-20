# 起飞稳定性实验（2026-09-17）

## 结论与边界

本轮完成三次实际起飞和一次被飞控拒绝的尝试。没有调整 PID、关闭预检或发送水平移动。历史大幅俯仰晃动未复现，不能宣称已解决。修正了原生起飞海拔参数及提前悬停逻辑；最终组合修改的飞行验证被 PX4 解锁拒绝阻断。

## 实验

通过现有服务 `/api/tool` 调用 `drone_takeoff(altitude=3)`，不经过 LLM 规划。探针检查仿真身份、空闲、已落地、未解锁及心跳；起飞返回后观察约 10 秒并降落。HTTP 遥测约每 0.6–1 秒一次，不能排除采样间的短时峰值。

| 数据文件（logs/） | 代码条件 | 结果 |
| --- | --- | --- |
| takeoff_probe_20260917_110701.jsonl | 原逻辑 | 原生爬升至约 2 米后回落至约 1.67 米，OFFBOARD 补升，观察结束 2.819 米；pitch −0.332°～0.052° |
| takeoff_probe_20260917_111031.jsonl | 原逻辑重复 | 同样经过 OFFBOARD，观察结束 2.811 米；pitch −0.252°～0.212° |
| takeoff_probe_20260917_111738.jsonl | 只修正海拔换算 | 实际 param7=51.807494841866195 米；到约 2.55 米时原逻辑调用 hover，提前切 LOITER，观察结束 2.219 米 |
| takeoff_probe_20260917_112306.jsonl | 海拔换算 + 不提前 hover + 稳定等待 | 原生和兜底路径都在解锁阶段被 TEMPORARILY_REJECTED 拒绝，没有实际起飞；不能评价爬升逻辑 |

第一轮最大采样水平位移约 3.2 厘米，最大高度 3.094 米。第二轮及第三轮也正常降落锁定。第四轮的 `.history.json` 保留收到的姿态、位置、速度历史，但这是未起飞尝试的数据，不是成功飞行的高频验证。

## 保留的代码改动

`src/modules/mavlink_controller.py`：

- NAV_TAKEOFF param7 使用 AMSL；由新鲜全局海拔和本地 NED z 计算 `global_alt + local_z + requested_altitude`，与后续 z=-altitude 目标一致。
- 使用命令目标机的遥测，不误读当前 UI 选中机；缺失、非有限或超过 1 秒的参考数据不发送原生起飞命令，保留既有 OFFBOARD 兜底。
- 高度首次越过原有到达阈值时，不再调用 hover 打断仍在 TAKEOFF 模式中的爬升。等待飞控进入 LOITER/POSCTL，垂直速度不超过 0.2 m/s 且本地位置新鲜，持续 1 秒后返回。
- 原生等待支持取消/失联退出；取消或失联后不继续启动 OFFBOARD 兜底。
- 日志记录发出的本地目标/海拔参数和原生稳定完成分支。

没有修改通用 move_to_position 的轨迹、PID 或起飞高度阈值。原生起飞不足时的已有 OFFBOARD 补升路径仍在，且该路径本身尚无稳定等待改造。

参考：PX4 v1.14.4 `src/modules/navigator/navigator_main.cpp` 与 `takeoff.cpp`。原生 NAV_TAKEOFF 将 param7 直接用作绝对高度；原先直接发送 3.0 与本地 3 米语义不符。

## 阻断与最终状态

`logs/takeoff_settled_runtime.log` 记录命令 400（解锁）被 TEMPORARILY_REJECTED 拒绝，而非命令 22（起飞）ACK 超时。没有完整原始 ACK 数据留档，STATUSTEXT 为空。后续 SYS_STATUS 显示 RC、电池健康位为 false；这不能被认定为无害的 SITL 固有状态，也不足以确定此次拒绝原因。未关闭检查、强制解锁或改传感器参数。

失败尝试留下的地面 OFFBOARD 模式已通过正常模式命令恢复 LOITER；返回遥测确认未解锁、已落地、心跳正常。没有继续反复尝试解锁。

## 验证

- 新增海拔/原生完成测试 13 项与 ACK 序列测试 1 项全部通过。ACK 序列测试仅验证拒绝不会误报超时，不证明实际拒绝来源。
- 海拔测试、多机测试、原始图像及感知相关测试组合：69 passed（不含后来新增 ACK 测试）。
- 包含 flight_preemption 的组合：30 passed，1 failed；失败为既有 `test_airsim_wait_move_arrival_preempts_on_stop`，约 6.5 秒，要求小于 2 秒。单独重跑仍失败。
- 未完成最终组合修改的成功起飞验证，不报告飞行稳定性修复完成。

探针：`scripts/probe_takeoff_stability.py`；需人工明确执行，不是自动化计划。
