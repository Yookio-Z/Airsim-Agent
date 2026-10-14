# ReKep: Spatio-Temporal Reasoning of Relational Keypoint Constraints for Robotic Manipulation

- **链接**：https://arxiv.org/abs/2409.01652
- **作者**：Wenlong Huang, Chen Wang, Yunzhu Li, Ruohan Zhang, Li Fei-Fei
- **时间**：2024-09-03（v1）；2024-11-12（v2）
- **状态**：✅ 摘要级已核验（2026-10-13）；⚠️ ε/回退机制待核正文

## 一句话

用**关系关键点约束（ReKep）**表示操作任务：约束是**映射一组 3D 关键点到数值代价的 Python 函数**；
把任务写成"约束序列"后，用分层优化解出动作序列（SE(3) 末端位姿），并配一条实时"感知-动作"循环；
为避免为每个新任务手写约束，用大视觉模型 + VLM **从自然语言指令与 RGB-D 观测自动生成 ReKep**，
无需任务专用数据与环境模型。

## 逐字引用

> "ReKep is expressed as Python functions mapping a set of 3D keypoints in the environment to a numerical cost."

> "by representing a manipulation task as a sequence of Relational Keypoint Constraints, we can employ a hierarchical optimization procedure to solve for robot actions"

> "we devise an automated procedure that leverages large vision models and vision-language models to produce ReKep from free-form language instructions and RGB-D observations"

> "all without task-specific data or environment models"

（来源：arXiv 摘要页，2026-10-13 抓取）

## ⚠️ 待核验（来自调研总结，摘要未包含）

- "阶段在末端到达子目标 ε 内即推进"（`if distance(e_t, e_g) < ε then i←i+1`）
- "路径约束被违反时回退到上一阶段"（backtracking）
- 关键点跟踪频率（~20 Hz）与求解开销

写作若要写"子目标容差 + 回退"这类细节，请先核正文（第 3-4 节）。

## 与本项目的关联

**这是"判据是数据/代码、属于任务"的最直接先例**：我们把它从机械臂搬到了 UAV——
判据不是每任务一段 Python，而是白名单内的参数化元组（`metric + params + tolerance + hold_s + required`），
由规划器写进计划、运行时评估。差异点（可写进创新点）：
1. 我们把"约束函数"降维成**闭集判据**（安全、无需沙箱执行 LLM 生成的代码）；
2. 我们额外处理"**卡住了怎么办**"（无进展监控，`ideas/02`），ReKep 依赖求解器与人工打分。

## 能引出的论点

1. 任务自带约束/完成条件、运行时只求解与评估——这条路线在文献里成立。
2. 对 UAV，闭集判据契约比"生成代码"更可审计（可写进"安全性/可解释性"章节）。
