# 循环上下文设计（Agent Loop 每轮给模型什么）

> 起因：2026-10-13 实跑 `飞向画面里那辆浅蓝色汽车，靠近后悬停` —— 6 轮模型往返只观察不动作，
> 最后死在一次"空回复"上。回看每轮注入：约 14.8k tokens，其中工具卡片 5.8k、记忆 5.6k（实测）。
> 这份文档把"每轮注入什么、裁剪顺序、参考了谁"定成契约，并由测试锁定。
> 参照实现：`C:\Users\26494\Desktop\pi`（Pi Agent Harness，TypeScript/MIT）。

## 0. 先回答"空回复是不是输入体量太大"

不确定是唯一原因，但它确实是**风险因子**：空回复（无内容、无工具调用）的典型成因是内容过滤、
首 token 前截断、服务端抖动。上下文越长，前两者越容易发生。所以我们的处理是两条腿：
- 重试（`LLMEmptyResponseError` → `_NATIVE_EMPTY_RETRIES = 2`），一次抖动不再让任务失败；
- 把每轮注入从 ~14.8k 压到 ~5–6k（见 §4），降低触发面。

## 1. 从 Pi Harness 借的四条规则

| Pi 的做法（源码） | 我们照搬的规则 | 我们的实现 |
|---|---|---|
| 上下文就是消息轨迹，工具结果是普通消息（`agent-loop.ts`） | 轨迹必须以"完整一步"为单位可读、可压缩 | `loop_state.steps_digest`：一行一步（序号/工具/成败/入参/关键数值）+ 最近 3 步完整返回 |
| 阈值压缩：`contextTokens > contextWindow − reserveTokens`（reserve 16384），保留最近约 20000 tokens（`compaction/compaction.ts`） | 能力与当前观察**永不裁**；可再生的历史/记忆先裁 | `ContextBudget.PRIORITIES`：command/observation 为 ESSENTIAL；memory(5)/guidance(6) 先让位 |
| 裁点绝不落在工具调用/结果中间（`findValidCutPoints` 明确排除 `toolResult`） | 摘要不切半步：不会出现"半条工具结果" | `_steps_digest` 以结果行为单位；`recent_results` 整条进整条出 |
| 用 provider 报的 usage 记账（`estimateContextTokens` = usage + 尾部估算） | token 账要能对得上，且压缩要有观测 | 已记录 `last_usage`/`_record_usage`；**阈值驱动的自动压缩待接**（见 §6） |

## 2. 刻意不照搬的部分

- **不用 LLM 做摘要**。Pi 的压缩会调一次模型生成结构化摘要；无人机循环 15–40 s/次往返，
  且摘要本身可能被过滤/抖动（正是我们刚踩的坑）。改用**确定性 digest**：零延迟、零 token 成本、可测试。
- **不把 transcript 原样发给模型**。我们的工具结果是结构化大 JSON（图片 base64、逐点深度数组），
  "原样入消息"会立刻爆预算（一张照片 ≈ 30 万 token，实测）。改为"最近 3 步完整（图片已裁）+ 全部步骤一行摘要"。
- **不引入会话分支/多 lane**（Pi 的 branch summarization 面向编码 agent 的探索式长会话），
  我们的会话边界是"一次任务"，退出即结束。

## 3. 每轮注入什么（当前契约）

| 段 | 内容 | 优先级 | 超预算时 |
|---|---|---|---|
| `operator_command` | 操作员指令原文 | command（永不裁） | — |
| `output_schema` | 决策 JSON 形状提示 | command | — |
| `observation` | 世界状态 / 上一步结果 / 帧龄 | observation（永不裁） | — |
| `backend_capabilities` | 后端能力 | observation | — |
| `loop_state.steps_digest` | 每步一行，**上限 24 行**，最老先丢 | recent | 截断 |
| `loop_state.recent_decisions` | 最近 4 条决策 | recent | 截断 |
| `loop_state.recent_results` | 最近 3 条完整返回（图片已换成占位） | recent | 截断 |
| `available_tool_cards` | **仅 JSON 模式**：精简卡片（name/purpose/when_to_use/inputs/preconditions/not_for/kind） | tool_cards | 省略 |
| `skill_guidance` | 技能目录（名称 + 一句话） | guidance | 省略 |
| `memory_snapshot` | session + 最近 3 条任务摘要 + guidance（工具 3/3、技能 2、风险 2） | memory | 省略 |
| `conversation_context` | 会话上下文（有则带） | memory | 省略 |

**原生工具调用模式下不发 `available_tool_cards`**：工具名/描述/参数 schema 已由 API 的
`tools` 参数下发，正文再重复一份是纯浪费（实测 ~5.8k tokens/轮）。系统提示里的话术也改成了
"use only the tools provided to you (native list or available_tool_cards)"。

## 4. 实测数字（改动前 → 后）

| 项 | 前 | 后 |
|---|---|---|
| 工具卡片（13 张，JSON 模式） | 13 143 字符 | 6 126 字符（-53%） |
| 工具卡片（原生模式） | 同上 | **整节省略**（≈ -3.3k tokens/轮） |
| 记忆快照 | 23 764 字符 | 2 056 字符（-91%） |
| 20 步轨迹 | 5 730 字符（且随步数线性增长） | 2 701 字符 + 24 行硬上限 |
| 每轮合计（原生模式，典型） | ≈ 14.8k tokens | ≈ 5–6k tokens |

记忆里被移出逐轮注入的 `facts` / `lessons` 是**自然语言档案**：既长又容易过期（旧记录里还写着
"用目标检测确认"），改为由模型调 `memory_recall` 按需取；`guidance` 的工具/技能推荐已在
`AgentMemory.guidance()` 里按注册工具表过滤，不再可能出现已删除的工具名。

## 5. 规则（代码即契约，测试锁定）

1. **能力声明只发一次**：原生模式走 API `tools`；JSON 模式发精简卡片。
2. **历史只增摘要不增体积**：`steps_digest` 上限 24 行，最老先丢，序号保持真实步号。
3. **记忆是提示不是档案**：逐轮只给"这次决策用得上"的几条，档案按需 `memory_recall`。
4. **关键数据不裁，可再生数据先裁**：command/observation 永不裁；历史 → 记忆 → 指导依次让位。
5. **裁点以整步为单位**：不许出现半条工具结果。
6. **图片永不进上下文**：`image_base64` 一律换成 `has_image` / `image_base64_bytes` 占位。

回归测试：`tests/test_loop_context_budget.py`（原生/JSON 卡片、轨迹摘要上限与内容、记忆范围与体量、图片占位）。

## 6. 本轮落地（2026-10-13 第二批）

1. **usage 驱动的自动压缩（可观测 + 自适应）**：用 provider 报的上一轮 `prompt_tokens`
   对比模型窗口，超过 70% 就切收紧档——摘要缩到 8 行、只留最近 1 条完整结果；同时
   运行层发一条可见事件"上下文接近模型窗口（x/y tokens）：本轮起压缩历史…"（每次运行
   最多一次）。这是 Pi `shouldCompact` 的确定性版本：只压缩，不调模型。
2. **按任务类型收窄工具菜单**：任务类型的信号是"被激活过的技能"——技能声明了自己的
   `subtools`，那就是这类任务需要的工具集。收窄规则：常备集（链路/安全/基本机动/
   基本感知）∪ 本轮实际用过的工具 ∪ 已激活技能的 subtools；没有技能激活时**不过滤**。
   ⚠️ 收窄只作用于**提示词与 API tools 菜单**；决策白名单仍按全量卡片算，所以某个工具
   即使没进菜单、模型仍然叫得出名字，也不会被误判成"不可用工具"而被拒绝。
3. **记录层的崩溃恢复**：进程启动时把上一次进程留下的 `running/executing` 记录标成
   `interrupted`（界面不再永远转圈），幂等、且不会碰本次进程的在途 run。

## 7. 刻意不做：飞行任务的自动续跑

用户的第三项是"会话落盘 + 压缩条目（崩溃恢复）"。**落盘与恢复"记录"已经做了；恢复"执行"
刻意不做**，原因是飞行指令是非幂等的：

- 崩溃后飞机的真实状态未知（可能已 RTL/降落/被别人接管），重放"继续靠近/继续降落"等于
  盲发飞行指令；
- 我们已有的安全语义（审批门、包线看门狗、后端 generation 校验、连接熔断）都以"当前进程
  的活链路"为前提，跨进程续跑会把它们全部绕过；
- Pi 的 resume 面向的是文件编辑这类可重放副作用，这一点我们**不照搬**。

如果将来确实要做"续跑"，前提是先有：动作幂等性分类（读/写/飞）、后端 generation 与
飞控状态一致性校验、以及"续跑也必须重新过一遍审批"的流程。在那之前，正确行为是
**如实报告中断**，让操作员重新下指令。

## 8. 仍然待定

- 卡片进一步按"本任务用得到"过滤到工具级（现在只按技能 subtools 收窄一层）。
- 把收紧档的阈值/档位做成可配置（现在常量 70% / 8 行 / 1 条）。

