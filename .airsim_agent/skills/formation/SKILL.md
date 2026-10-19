---
name: formation
display_name: 编队与区域覆盖指导
status: guidance
type: guidance
description: 当操作员要求多机编队飞行、集群移动、队形变换或多架无人机区域覆盖时使用本技能——例如"菱形编队飞到(50,50)"、"三角队形"、"编队巡航"、"覆盖扫描 100x50 区域"、"三机分区搜索整个场地"、"四机旋转45度"，或任何指定队形（line / v_shape / triangle / diamond / square / hexagon / circle / arrow）或要求多机覆盖一个区域的指令。覆盖机数与队形的搭配、间距参数的实际含义与最小安全间距、每轮一个意图的纪律、动作之间的状态确认、以及 stable/percent 完成判据。仅在任务涉及 2 架及以上时使用；至少 2 架、仅 airsim / px4_mavlink 后端可用。
required_capabilities: [flight_control]
subtools: [formation_command, drone_get_status]
cost: medium
risk: high
---

# 编队与区域覆盖指导

## 目的

指导 AirSim 与 PX4 MAVLink 后端上的多机编队飞行与区域覆盖任务。

**为什么是"一个命令管一队"而不是逐机下原子指令**：编队是一个持续约束——队形要在整个飞行
过程中每一刻都成立。`formation_command` 背后是一个 10Hz 的确定性控制环（模型看不到逐 tick
状态），所以模型只需要每轮发一个高层意图；逐机发 `drone_fly_to` 无法在两次决策（10–20 s）
之间维持队形，也没有间隔约束。策略归你（什么队形、去哪、什么时候换），维持队形归控制器。

`formation_command` 至少需要 2 架无人机，且在 px4_ros2 后端上不可用。

## 使用时机

- 操作员要求编队飞行、集群移动或指定队形（"菱形编队飞到 (50,50)"、"三角队形"、"编队巡航"）
- 操作员要求用多架无人机覆盖或扫描一个区域（"覆盖扫描 100x50 区域"、"三机分区搜索整个场地"）
- 要求对已有队形做变换（"旋转 45 度"、"间距拉大一倍"）

## 队形与机数：效果由公式决定（先读这张表）

八种队形都是**按机数 N 和间距计算的公式**，不是固定点集——所以同一个名字在不同机数下
效果不同，**机数和队形要搭配**。相邻实际距离一列决定了会不会触发最小间距（2 m 硬限制）。

| 队形 | 排布规则 | 相邻实际距离 | 机数注意 |
|---|---|---|---|
| `line` | 一字横排，中心居中 | = spacing | 任何 N 都整齐；8 机 5 m 排开就宽 35 m |
| `v_shape` | 1 架领头 + 左右两臂逐级后掠 | ≈ 0.85 × spacing | **偶数机会左右不等**（多出的一架在右臂）；要对称 V 用奇数 N |
| `triangle` | 1 / 2 / 3 … 逐行排（行距 = spacing） | ≈ 1.0–1.1 × spacing | 只有 N=1/3/6/10… 是完整三角形，其他 N 最后一行是部分行 |
| `arrow` | 领头 + 左右交替、幅度渐增的纵列 | ≈ 1.1 × spacing | 是"雁列"不是标准箭头；幅度随 N 增大 |
| `square` | 四角环（±s, ±s）；超过 4 架先放中心再逐圈 | = 2 × spacing | **只有 N=4 是完整方形**；N=2/3 是缺角环且整体偏离中心 |
| `diamond` | 菱形四角 (±s,0)、(0,±s)；超过 4 架逐圈 | ≈ 1.41 × spacing | N=4 完整；缺角时偏中心 |
| `hexagon` | 正六边形（半径 = spacing）；超过 6 架逐圈 | = spacing | N=6 完整 |
| `circle` | 正 N 边形均匀分布，半径 = spacing×N/2π | ≈ spacing（弧长） | N≥2 都均匀；小 N 时近似一字 |

**搭配建议**：3 机 → `triangle` / `v_shape` / `line`；4 机 → `square` / `diamond`；
5 机 → `v_shape`（不对称）或 `circle`；6 机 → `hexagon` / `triangle` / `square`。
**间距**：最小相邻距离 2 m 由控制器硬性拒绝（`set_formation` / `scale` 会报错）；默认
5 m 稳妥；用 `v_shape` / `arrow`（系数 <1）时 spacing 不要低于 3 m。

## 可以调什么

| 参数 | 说明 |
|---|---|
| `formation_type` / `spacing` | 队形与名义间距；实际相邻距离见上表 |
| `altitude`（takeoff） | 起飞高度，**默认 10 m——显式给一个值**，别靠默认蒙 |
| `action=move_center, x, y[, z]` | 整队平移；不传 z 保持当前高度 |
| `action=rotate, angle_deg` | 绕队形中心旋转；正角 = 北→东（俯视顺时针） |
| `action=scale, scale_factor` | 整体缩放；会先检查最小间距，违反则被拒绝 |
| `area_shape`/`area_width`/`area_height`/`area_radius` | 覆盖区域（矩形或圆形；上限 500 m） |
| `resolution` | 覆盖网格步长（米），默认 5；越小航点越多 |
| `partition` | `balanced` / `stripe` / `quadrant`（多机怎么切区域） |
| `path_algo` | `boustrophedon`（往复）/ `spiral` / `nearest` |
| `area_altitude` / `coverage_speed` | 覆盖高度（默认 10 m）与速度（默认 3 m/s） |

**不能调的**：八种之外的任意自定义队形（几何入口目前只有这八种）。需要别的形状时，挑最接近
的一种再用 `rotate`/`scale` 调整，或改代码（维护者注见文末）。

## 工作流程

```
1. 确认机群:  drone_get_status（不带 vehicle_name）→ 一次拿到全部机名与状态
2. 指定参与:  formation_command(action=set_drones, vehicle_ids="drone_0,drone_1,...")
3. 设置队形:  formation_command(action=set_formation, formation_type=<见上表>, spacing=5.0)
              ← 改队形/间距前必须处于 idle（先 hover_all 或 land_all），否则会报错
4. 起飞:      formation_command(action=takeoff, altitude=<显式高度>)
              → 返回后 10Hz 控制环开始维持队形
5. 收敛确认:  formation_command(action=status) 直到 stable=true
              （stable = 全部在空中且距各自目标 ≤0.5 m；起飞约 10 s，平移 50 m 约 10–20 s）
6. 移动/变形: formation_command(action=move_center, x=, y=) 整队平移
              formation_command(action=rotate, angle_deg=) / (action=scale, scale_factor=)
              → 每步之后同样用 status 等到 stable
7. 覆盖任务:  formation_command(action=coverage_plan, area_shape=rectangle, area_width=,
              area_height=, resolution=, partition=, path_algo=, area_altitude=)
              → 先看返回的 total_waypoints（为 0 说明区域或参数不对）
              formation_command(action=coverage_start)
              → 轮询 action=status 的 progress.percent 直到 100
8. 收尾:      formation_command(action=land_all)；想暂停用 hover_all
```

## 核心规则

- **一次一个意图**：每轮只发一个 formation_command action；不要在上一动作还没 `stable`
  时就发下一个——连发会让状态确认失去意义，出错时也说不清是哪一步。
- **步骤间验证**：move/rotate/scale 后先 `status` 确认再继续。
- **不要混用单机工具**：编队激活期间 `drone_takeoff`/`drone_fly_to`/`drone_move_relative`
  等会被安全层拦截（返回 BLOCKED）。要停编队：`action=hover_all` 或 `land_all`。
- **完成判据**：任务 goal 可声明 `{"metric": "formation_stable"}`；runtime 校验 status
  返回的 `stable=true` 才接受完成。
- **失败处理**：takeoff/land_all 返回 `failed` 列表时，对失败的机单独重试**一次**；
  连续失败就 `stop` 并上报。status 的 `events` 字段给出 auto_stop/shutdown 原因。
- **间距被拒**：`set_formation`/`scale` 报 "min pairwise distance … < 2.0m" 时，改大
  spacing（注意上表里各队形的系数），不要重试同一组参数。
- **紧急情况**：急停会触发全机悬停（无需你操作）；任务结束时若编队仍在飞，runtime
  会自动 hover_all 并停止控制环。
- **安全边界**：最小间距 2 m；覆盖区域上限 500 m；速度上限由安全层钳制。

## 示例（照抄即可）

**三机三角队形，间距 5 m，飞到 (50,50) 悬停**
```
formation_command(action=set_drones, vehicle_ids="drone_0,drone_1,drone_2")
formation_command(action=set_formation, formation_type="triangle", spacing=5.0)
formation_command(action=takeoff, altitude=10.0)
→ status 轮询到 stable=true
formation_command(action=move_center, x=50, y=50)
→ status 轮询到 stable=true → 汇报
```

**四机菱形，间距 8 m，旋转 45°**
```
set_drones → set_formation(formation_type="diamond", spacing=8.0) → takeoff(altitude=12.0)
→ 等 stable → rotate(angle_deg=45) → 等 stable
（菱形相邻距离 ≈ 1.41×8 ≈ 11.3 m；想更紧凑时 spacing=4 也安全：相邻 ≈5.7 m）
```

**两机覆盖扫描 60×40 m 区域**
```
set_drones(2 架) → takeoff(altitude=12.0)
coverage_plan(area_shape="rectangle", area_width=60, area_height=40, resolution=5,
              partition="balanced", path_algo="boustrophedon", area_altitude=12.0)
→ 查看 total_waypoints → coverage_start → 轮询 status 的 progress.percent 到 100 → land_all
```

**反例（表里那列"机数注意"的用法）**：操作员要"三机方块队形"——`square` 在 N=3 时是缺角环
且整体偏离中心。正确做法：改用 `triangle`，或说明原因并建议加第 4 架。

## 不适用

- 单机任务（用 drone_* 工具和对应的单机技能）
- PX4 ROS2 后端（`formation_command` 未注册）
- 需要机间避碰的真实场景（当前为 AirSim 仿真 / PX4 SITL 设计；真机编队需额外研究）
- 近距感知类任务（"靠近那个门框"这类 2–3 m 高度的单机任务）——那是单机技能的领域

---
维护者注：控制器实现在 `src/modules/formation/controller.py`（队形几何 `formation_offsets`
是纯函数）；工具注册在 `src/agent/tool_executor.py::_ensure_formation_tools`。
