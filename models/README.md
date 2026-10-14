# models/ —— 旧目标检测权重（**当前未使用**）

本目录里的 `.pt` 文件是目标检测（YOLO/Ultralytics）时代的权重：

| 文件 | 说明 |
|---|---|
| `yolov8s.pt` | 通用检测权重（COCO 80 类），当年 `airsim_detect_objects` 用的就是它 |
| `yolov8s-worldv2.pt` | 开放词汇检测权重（实验用） |

## 现状（2026-10-13 决议）

产品**已放弃目标检测**：感知路线是"**视觉模型给语义 + 深度给距离 + 位姿投影给坐标**"。
因此：

- 没有任何工具会加载这些权重（`perception_start` / `airsim_detect_objects` 已删除）；
- 感知轴的检测线程默认不启动，也没有入口能启动它；
- `src/modules/yolo_detection.py` 是**休眠能力**，无生产调用方（模块头部有说明）。

## 是否要删

- 这些权重**没有被 git 跟踪**（`.gitignore` 里 `*.pt` 一律排除），删掉不影响仓库、也不影响构建；
- 如果确定不再做检测实验，直接删除本目录即可；如果还想留着做对照实验（例如"有检测 vs 无检测"
  的毕设对比），把它们留在本地、按需手动加载，不要挂回 Agent 工具面。

## 相关文档

- [`../docs/agent_tool_skill_inventory.md`](../docs/agent_tool_skill_inventory.md)（顶部"决议后"横幅）
- [`../docs/vision_depth_fusion.md`](../docs/vision_depth_fusion.md)（为什么深度+视觉能替代检测，
  以及"检测真值只用于评测对照"的边界）
