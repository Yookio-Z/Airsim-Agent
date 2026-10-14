# Vision-Language Models as Success Detectors（SuccessVQA）

- **链接**：https://arxiv.org/abs/2303.07280
- **作者**：Yuqing Du, Ksenia Konyushkova, Misha Denil, Akhil Raju, Jessica Landon, Felix Hill,
  Nando de Freitas, Serkan Cabi
- **时间**：2023-03-13（v1）
- **状态**：✅ 摘要级已核验（2026-10-13）；⚠️ 数值待核正文（含一处**口径修正**，见下）

## 一句话

把"判定任务是否成功"重构成**视觉问答**（SuccessVQA）：用预训练 VLM（Flamingo）+ 人类奖励标注
构造成功检测器，在三个差异极大的域（模拟家庭交互 agent / 真机操作 / 人第一视角视频）验证，
并在分布外测试中优于定制的奖励模型。

## 逐字引用

> "Detecting successful behaviour is crucial for training intelligent agents."

> "we treat success detection as a visual question answering (VQA) problem, denoted SuccessVQA."

> "the proposed method is able to outperform bespoke reward models in out-of-distribution test scenarios"

> "success detection on unseen real videos presents an even more challenging generalisation task warranting future work."

（来源：arXiv 摘要页，2026-10-13 抓取）

## ⚠️ 口径修正（重要，别写错）

- 本次调研曾总结为"零样本 Flamingo 判成功 ≈ 随机（~50%）"。**摘要里没有这句话**，也没有
  "微调出二分类检测器"的表述。摘要只说用预训练 VLM + 人类奖励标注，并把成功检测当作 VQA。
- 想用"零样本接近随机"这个数字，必须去正文/表格核实（否则改写成"该工作表明成功检测并非
  零样本可直接解决，需要标注与适配"）。

## 与本项目的关联

- 支撑"**用 VLM 判语义完成条件**"是文献里成立的做法（`ideas/03`）。
- 它的代价（人类奖励标注）我们没有 → 所以我们走"中立提问 + 证据引用 + 多样本多数"的零训练版本，
  并明确**不承诺**与训练过的检测器同等精度。

## 能引出的论点

1. 成功检测可以作为 VQA 来问，但跨域泛化是已知难点（引用末句）。
2. 我们的判据契约把"语义判定"限定在**任务声明**的具体条件上，避免开放式的"任务成功了吗"。
