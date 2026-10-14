# 长尾条目（表格式摘要，引用前请核验）

> 本页只做索引。状态：✅ 已核验 / ⚠️ 仅摘要（调研总结，未见原文）/ ❓ 未核验。

## 开源 VLA / 机器人策略：终止与成功判定

| 项目 | 策略自终止？ | 独立验证器？ | 判据谁写 | 成功谁打分 | 状态 |
|---|---|---|---|---|---|
| OpenVLA / OpenVLA-OFT | 否 | 无 | benchmark/实验者 | LIBERO 环境谓词；真机人工 0/1/0.5 | ⚠️ |
| openpi（π0 / π0.5 / π0-FAST，arXiv:2410.24164 / 2504.16054） | 否（π0.5 输出子任务文本） | 无 | 人工评分要点 | 带部分分的评分要点（1.0=完全成功） | ⚠️ |
| Octo（arXiv:2405.12213） | 否 | 无 | benchmark/人工 | 试验成功率 | ⚠️ |
| RDT-1B（thu-ml/RoboticsDiffusionTransformer） | 否 | 无 | benchmark/人工 | 成功率 | ❓ |
| NVIDIA GR00T N1/N1.5（arXiv:2503.14734） | 否 | 无 | benchmark/人工 | 仿真谓词 | ⚠️ |
| LeRobot（huggingface/lerobot） | 框架级 only | 无 | 环境作者 | `info["is_success"]`（环境真值） | ⚠️ |
| SayCan（say-can.github.io） | **是**（计划以 "Done" 收尾） | 只有可行性价值函数 | LLM 写计划 | 人工 | ⚠️ |
| Inner Monologue（arXiv:2207.05608） | 规划器在反馈下决定停止 | **是**（学习出来的成功检测器，结果以文本反馈注入） | 检测器训练得到 | 任务完成率 | ⚠️ |
| Code as Policies（arXiv:2209.07753） | 隐式（程序结束） | 无 | LLM 写程序含检查 | 仿真/人工 | ⚠️ |
| VoxPoser（voxposer.github.io） | 隐式 | 无 | LLM/VLM 代码 | 仿真+真机 | ⚠️ |
| MOKA（arXiv:2403.03174） | 否（每阶段 100 步上限） | 无 | VLM 拆子任务 | 人工专家 | ⚠️ |
| Voyager（arXiv:2305.16291） | 独立 critic 提示词，**最多 4 轮**后判失败 | critic 是 LLM | LLM 写 critic 提示 | 环境 | ⚠️ |
| VLA-SCT（arXiv:2602.01811） | **终止外挂** | 无（启发式终止逻辑） | 框架自带 | LIBERO | ✅ 见专页 |

## 任务规约形式（判据怎么表达）

| 形式 | 具体形状 | 状态 |
|---|---|---|
| PDDL 目标谓词（PlanSys2；LLM+P arXiv:2304.11477） | `(:goal (and (at drone pad) (landed)))`；经典规划器给计划或失败 | ⚠️ |
| HTN / HDDL（arXiv:1911.05499、2306.07353） | 方法分解 + 子任务效果；完成=所有分解任务达成 | ❓ |
| 行为树后置条件 / SMACH 结果（arXiv:1709.00084、BT.CPP、ROS SMACH） | 叶子返回 SUCCESS/FAILURE/RUNNING；后置条件在返回瞬间检查 | ⚠️ |
| ProgPrompt 断言（progprompt.github.io） | LLM 写"注释+动作+断言"；断言检查前置条件并触发恢复 | ⚠️ |
| ReKep 约束函数（arXiv:2409.01652） | Python 函数 `f: R^{K×3} → R`，代价 ≤ 0 视为满足 | ✅ 见专页 |

## 运行时监控与统计

| 机制 | 作用 | 状态 |
|---|---|---|
| N 连测 + 滞回裕度（Schmitt；本仓库包线守卫就是 3 连测） | 廉价防抖，防阈值抖动 | ⚠️ |
| SPRT（Wald）/ CUSUM（Page） | 有 α/β 控制的序贯检验 / 变化点检测（"不再改善"） | ⚠️ |
| 停滞检测（arXiv:2101.12054；PUL-SLAM arXiv:2511.04180） | "连续 K 次无进展 → 升级策略"的模板 | ❓ |
| Conformal 风险控制（arXiv:2208.02814；anytime-valid 2602.04364；KnowNo 2307.01928） | 零训练给出"假成功率 ≤ α"的统计保证 | ⚠️ |
| 诚实预算终态（WebArena 2307.13854 / OSWorld 2404.07972 / AbstentionBench 2506.09038） | 预算耗尽要单独记为 timeout/未验证；模型不会主动说"我不知道" | AbstentionBench ❓ |
| 三态/多态终态 + 逐判据账本（RoboReward 2601.00675 的分级、VRR-Stop 2607.17641 的边际增益停止） | 成功/未验证/预算耗尽分开报 | ⚠️ |

## VLM-as-judge 的已知坑（写作"局限性"章节很好用）

| 现象 | 证据 | 状态 |
|---|---|---|
| judge 会顺着 agent 的说法说成功（谄媚） | SycEval arXiv:2502.08177（谄媚率 58.19%；**预先下结论时 61.75%**；持续率 78.5%） | ⚠️ |
| 位置/长度/自我偏好偏差 | MLLM-as-a-Judge arXiv:2402.04788（LLaVA 88.2% 复制 "ABCD" 顺序；较长答案得分 +0.6/+0.75） | ⚠️ |
| 琐碎视觉题也会翻车 | BlindTest arXiv:2407.06581（四个头部 VLM 平均 58.07%） | ⚠️ |
| 强制证据/独立复问可缓解 | CoVe arXiv:2309.11495；POPE arXiv:2305.10355；Set-of-Mark arXiv:2310.11441 | ⚠️ |
| 多样本多数投票 | Self-Consistency 2203.11171；Universal Self-Consistency 2311.17311；SelfCheckGPT 2303.08896 | ⚠️ |
| "验证者 > 生成者" | 2408.03314、2408.15240、2305.20050；无外部反馈的自纠会退化（2310.01798） | ⚠️ |
