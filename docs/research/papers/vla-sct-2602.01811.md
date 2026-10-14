# From Knowing to Doing Precisely: A General Self-Correction and Termination Framework for VLA models（VLA-SCT）

- **链接**：https://arxiv.org/abs/2602.01811
- **作者**：Wentao Zhang, Aolan Sun, Wentao Mo, Xiaoyang Qu, Yuxin Zheng, Jianzong Wang
- **时间**：2026-02-02（v1）
- **状态**：✅ 已核验（2026-10-13 从 arXiv 摘要页抓取）

## 一句话

指出 VLA 的两个弱点——动作 token 与目标物存在空间偏差，以及**无法可靠识别任务完成**——
并给出训练无关（training-free）的 VLA-SCT 框架：数据驱动的动作精修 + **条件终止逻辑**
（conditional termination logic），在 LIBERO 上提升成功率并让"何时结束"变准。

## 逐字引用（写作可直接用）

> "they lack the ability to reliably recognize task completion, which leads to redundant actions
> and frequent timeout errors"

> "combining data-driven action refinement with conditional logic for termination"

> "ensuring accurate task completion"

（来源：arXiv 摘要页，2026-10-13 抓取）

## 为什么这篇对我们重要（**这是"无限逼近"的文献级印证**）

1. 2026-10-13 实跑：模型一路微调逼近目标，28 步撞上限、任务被中断（36 步 / 1027 s）。
   VLA-SCT 把同一现象写成了领域结论：**"无法识别完成" → 冗余动作 + 超时**。
2. 它把"终止"当成**独立于策略的外挂模块**（termination logic），这与我们的设计一致：
   判据由任务声明、运行时评估，策略本身不负责说"我做完了"。
3. 它是 **training-free** 的——和我们的约束（无训练数据、只有 LLM/VLM）相同，因此
   "运行时外挂终止逻辑"这条路线在文献里是有人走通的，不是我们的自说自话。

## 不要照抄的部分

- 它的"条件终止逻辑"是启发式模块、且面向机械臂（LIBERO）。我们是 UAV + 开放指令，
  因此把终止做成**任务声明的判据契约**（见 `ideas/01-criteria-contract.md`），而不是固定启发式
  ——这也正是我们相对它的差异点（可写进创新点：**把终止条件从启发式提升为任务契约**）。

## 我们能引出的论点

1. 策略不自终止是 VLA 的通病（引用本文）。
2. 我们的判据契约 = "termination as a task-declared contract"，是对其启发式终止的推广。
3. 我们的无进展监控（`ideas/02`）补上它没处理的另一半：**终止逻辑之外，"卡住了怎么办"**。
