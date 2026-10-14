# 开源 VLA / 机器人 Agent 项目考究：**谁决定"做完了"**

- **状态**：⚠️ 仅摘要（2026-10-13 由调研 agent 读仓库/文档/论文摘要汇总；逐条引用前请按表中的
  repo/论文链接复核——特别是"**没有**终止信号"这类否定性结论，最好自己 grep 一遍）
- **覆盖**：OpenVLA、openpi（π0/π0.5/π0-FAST）、Octo、RDT-1B、GR00T、LeRobot，
  以及"LLM 在上、策略在下"一系的 SayCan、Inner Monologue、Code as Policies、VoxPoser、ReKep、MOKA、Voyager。

## 核心结论（我们最看重的三条）

1. **没有任何可查证的开源 VLA 自己输出"停止/完成"信号**；终止一律是外部的——benchmark 环境谓词、
   步数上限、或人工评分。唯二例外都是外挂：SayCan 的计划级 "Done" 步、Inner Monologue 的训练过的
   成功检测器。→ 支撑我们的"判据外挂、策略不自终止"（见 `../ideas/01-criteria-contract.md`）。
2. **判据的表达形式普遍是"任务自带的规约"**：环境谓词（LIBERO 的 `_check_success`）、
   LLM 写的断言/检查函数（ProgPrompt、Code as Policies）、约束函数（ReKep）。
   没有一个是"运行时按任务类型写死一个常数"。
3. **重观察节奏是节拍式的**：π0 每 16/25 步重推理（H=50）、Octo 块长 4、GR00T 执行 8/16、
   `max_episode_steps` 普遍存在（1000 步级）。→ 对应我们的"每步复核 + `max_steps` 兜底"。

## 表格（详见调研原文；状态 ⚠️）

| 项目 | 策略停止信号 | 独立验证器 | 判据作者 | 成功打分 | 执行/重观察 |
|---|---|---|---|---|---|
| OpenVLA（arXiv:2406.09246） | 无 | 无 | benchmark/实验者 | LIBERO 环境谓词；真机人工 | 单步；OFT 块长 8/25 |
| openpi（π0 arXiv:2410.24164；π0.5 arXiv:2504.16054） | 无 | 无 | 人工评分要点 | 部分分评分要点 | H=50，每 16/25 步重推理；上限 1000 步 |
| Octo（arXiv:2405.12213） | 无 | 无 | benchmark/人工 | 试验成功率 | 块长 4，可选只执行首步 |
| RDT-1B（thu-ml/RoboticsDiffusionTransformer） | 无 | 无 | benchmark/人工 | 成功率 | 预测 64 步 |
| GR00T N1/N1.5（arXiv:2503.14734） | 无 | 无 | benchmark/人工 | 仿真谓词 | 执行时域 8/16 |
| LeRobot（huggingface/lerobot） | 框架级 only | 无 | 环境作者 | `info["is_success"]` | `while not done and step < max_steps` |
| SayCan（say-can.github.io） | **是**（计划 "Done"） | 可行性价值函数（非成功检测） | LLM 写计划 | 人工（101 任务：计划 84% / 执行 74%） | 技能级 |
| Inner Monologue（arXiv:2207.05608） | 规划器在反馈下决定（可停止） | **是**（学习型成功检测器，文本反馈） | 训练得到 | 任务完成率 | 每技能后重规划 |
| Code as Policies（arXiv:2209.07753） | 隐式 | 无 | LLM 写检查 | 仿真 | 程序内闭环 |
| VoxPoser（voxposer.github.io） | 隐式 | 无 | LLM/VLM | 仿真+真机 | MPC 重规划 |
| ReKep（arXiv:2409.01652，**已核摘要**） | 阶段推进（子目标约束） | 约束跟踪即检查 | VLM 生成约束函数 | 人工操作员 | 实时感知-动作循环 |
| MOKA（arXiv:2403.03174） | 无（阶段 100 步上限） | 无 | VLM 拆子任务 | 人工专家 | 子任务间重问 VLM |
| Voyager（arXiv:2305.16291） | 独立 critic + **4 轮上限** | critic（LLM） | LLM | 环境 | 技能库沉淀 |

## 我们借了什么 / 没借什么

**借**：
- "判据外挂、运行时评估"（全体一致）→ `ideas/01`
- "计划级显式终止步/断言/约束"（SayCan / ProgPrompt / ReKep）→ 我们的 `success_criteria`
- "节拍式重观察 + 硬预算"（π0/Octo/LeRobot）→ 我们的每步复核 + `max_steps`
- "独立 critic + 有界重试"（Voyager）→ 我们的无进展监控 `ideas/02`

**不借**：
- 训练型成功检测器（Inner Monologue / SuccessVQA 路线）：我们没有标注预算（见 `../papers/successvqa-2303.07280.md`）
- 生成代码式判据（ReKep / Code-as-Policies）：UAV 上不可审计、不可沙箱化，我们用闭集判据替代
- 需要模型内部的失败检测（隐状态探针等）：闭源 VLM API 拿不到

## 写作提示

- 引用"某项目没有终止信号"时，注明是**按 repo/文档核对**（并给日期），因为这是**否定性结论**，
  审稿人可能要求你自己再核一遍。
- 表格里 π0 的 `max_episode_steps=1000`、Octo 块长 4 这类数字来自调研摘要（⚠️），引用前去 repo 核对常量名。
