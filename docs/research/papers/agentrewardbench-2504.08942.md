# AgentRewardBench: Evaluating Automatic Evaluations of Web Agent Trajectories

- **链接**：https://arxiv.org/abs/2504.08942
- **作者**：Xing Han Lù, Amirhossein Kazemnejad, Nicholas Meade, Arkil Patel, Dongchan Shin,
  Alejandra Zambrano, Karolina Stańczak, Peter Shaw, Christopher J. Pal, Siva Reddy
- **时间**：2025-04-11（v1）；2025-10-06（v2）
- **状态**：✅ 摘要级已核验（2026-10-13）；⚠️ 数值待核正文

## 一句话

第一个评测"用 LLM judge 自动判定 agent 轨迹成败"是否可靠的基准：1302 条轨迹 × 5 个 web agent 基准 ×
4 个 LLM，每条都由专家复核成功/副作用/重复性；实测 12 个 LLM judge，**没有一个在所有基准上都最好**。

## 逐字引用

> "no single LLM excels across all benchmarks"

> "the rule-based evaluation used by common benchmarks tends to underreport the success rate of web agents"

> "highlighting a key weakness of rule-based evaluation and the need to develop more flexible automatic evaluations"

（来源：arXiv 摘要页，2026-10-13 抓取）

## ⚠️ 待核验的数值（写作前必须进正文确认）

- "没有任何 judge 精度超过 ~70%"
- "judge 倾向于高估成功"（GPT-4o agent 专家给 42.3 vs judge 给 50.0）
- 失效原因列表（grounding 不匹配 / 被 agent 自己的推理带偏 / 漏指令细节 / 误解动作意图）

这几条来自本次调研的摘要级总结，**摘要里没有**；引用前请读正文表格。

## 与本项目的关联

我们规定"完成由运行时按任务声明的判据评估，模型只能提议"。本文是这条纪律的**反面证据**：
如果让 judge（或模型自己）说了算，精度不足以支撑"宣布成功"这种高代价判断。

## 能引出的论点

1. 自动评测 agent 成败是已知难点，judge 精度天花板不高。
2. 因此我们的终态必须由**可机械评估的判据**决定，语义判据要有证据门控（`ideas/03`）。
