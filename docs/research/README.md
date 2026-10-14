# 资料库：论文考究 · 开源项目 · 创新点

> 用途：毕设/论文写作的资料底座。每条记录都带**验证状态**，写作时只引用 ✅ 的结论；
> ⚠️ / ❓ 的条目先核验再引用（或明确标注为"调研摘要，未见原文"）。
> 建立日期：2026-10-13。

## 目录结构

```
docs/research/
├── README.md              ← 本文件：索引 + 使用约定
├── references.bib         ← BibTeX，直接 \cite{} 用
├── papers/                ← 单篇论文考究（一页一篇）
│   └── _bulk-survey.md    ← 长尾条目（只做表格式摘要）
├── projects/              ← 开源项目考究（一页一项目/一族）
└── ideas/                 ← 我们的创新点 + 实验设计（可长成论文章节）
```

## 验证状态标签（**必写**，写作时按它设语气）

| 标签 | 含义 |
|---|---|
| ✅ 已核验 | 我直接读过摘要/正文片段，引用句为逐字（页面上抓到的原文） |
| ⚠️ 仅摘要 | 来自这次调研的摘要级总结，数值/细节未逐条核对正文 |
| ❓ 未核验 | 只在二手线索里出现，**引用前必须自己查** |

## 引用约定

1. 论文用 `references.bib` 里的 key（`\cite{vlasct2026}`），正文引用时带上 arXiv 号。
2. 我们自己实现的机制引用 `ideas/` 下的文件（例如 `\ref{idea:criteria-contract}`），
   并注明"本文实现"。
3. 每条记录写清**与我们项目的关联**（为什么收它）和**可引用的论断**（逐字原句 + 页面）。

## 怎么加新条目（给未来的自己）

1. 复制 `papers/_TEMPLATE.md` 的骨架，填标题/作者/链接/**逐字引用**/关联/可引用点。
2. 摘要里出现数字（成功率、精度）时：只有当你在原文里看到它才写 ✅；否则写 ⚠️ 并注明来源。
3. 同步往 `references.bib` 加条目；不确定的字段用 `note = {待核验}` 标出来。

## 当前索引

### 论文（papers/）

| 文件 | 主题 | 对本项目的作用 | 状态 |
|---|---|---|---|
| [vla-sct-2602.01811.md](papers/vla-sct-2602.01811.md) | VLA 缺乏可靠的任务完成识别 → 冗余动作与超时；训练无关的自纠+终止框架 | **我们"判据外挂、策略不自终止"这条设计的最强引证** | ✅ |
| [agentrewardbench-2504.08942.md](papers/agentrewardbench-2504.08942.md) | LLM judge 评估 agent 轨迹的可靠性基准 | 支撑"不能让模型自己宣布完成" | ✅（数值 ⚠️） |
| [successvqa-2303.07280.md](papers/successvqa-2303.07280.md) | 把"成功判定"重构成视觉问答；零样本 VLM 判成功接近随机 | 支撑"语义判据要 VLM + 证据/多样本" | ✅ |
| [rekep-2409.01652.md](papers/rekep-2409.01652.md) | 用 LLM 写的 Python 约束函数表达每阶段完成条件（含容差 ε） | 支撑"判据是数据/代码、属于任务" | ✅ |
| [abstentionbench-2506.09038.md](papers/abstentionbench-2506.09038.md) | 推理模型不会主动"弃权/说不知道" | 支撑"unverified 必须由运行时产生" | ✅ |
| [_bulk-survey.md](papers/_bulk-survey.md) | 长尾（PDDL/HTN/行为树/CUSUM/conformal/停滞检测/…） | 表格备用，引用前核验 | ⚠️/❓ |

### 开源项目（projects/）

| 文件 | 覆盖 | 状态 |
|---|---|---|
| [vla-projects-survey.md](projects/vla-projects-survey.md) | OpenVLA/π0/Octo/RDT-1B/GR00T/LeRobot + SayCan/Inner Monologue/CaP/VoxPoser/ReKep/Voyager：**谁决定停止、判据谁写、成功谁打分** | ⚠️（表格来自调研摘要，逐条引用前核验） |
| [pi-agent-harness.md](projects/pi-agent-harness.md) | Pi Agent Harness：上下文压缩/会话持久化/工具钩子（我们借了它的设计） | ✅（本地源码已读） |

### 创新点（ideas/）

| 文件 | 一句话 | 我们的证据 |
|---|---|---|
| [01-criteria-contract.md](ideas/01-criteria-contract.md) | 完成判据是**任务声明的可机械评估契约**，运行时只评估、不发明默认 | 已实现 + 测试 |
| [02-progress-monitor.md](ideas/02-progress-monitor.md) | 无进展监控 + 升级阶梯（提醒→否决重复→诚实收尾），只对声明的判据生效 | 已实现 + 测试 |
| [03-evidence-gated-vlm-verifier.md](ideas/03-evidence-gated-vlm-verifier.md) | 语义判据：中立提问 + 强制引用证据 + 多点复核 + 多样本 | 设计完成，待实现 |
| [04-vision-depth-cross-check.md](ideas/04-vision-depth-cross-check.md) | 视觉框 × 深度交叉验证（框内外一致性）、探测点溯源、飞过目标否决 | 已实现 + 测试 |
| [05-context-management.md](ideas/05-context-management.md) | 能力不裁/历史摘要/记忆按需取/菜单按任务收窄/用量驱动收紧 | 已实现 + 测试 |
