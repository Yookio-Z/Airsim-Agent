# Pi Agent Harness（上下文管理设计参考）

- **链接**：本地源码 `C:\Users\26494\Desktop\pi`；npm `@earendil-works/pi-agent-core` v0.85.1；MIT；
  作者 Mario Zechner（earendil-works），站点 pi.dev
- **状态**：✅ 已核验（本地源码已读：`packages/agent/src/agent-loop.ts`、
  `packages/agent/src/harness/compaction/compaction.ts`）
- **语言/形态**：TypeScript / npm only，无 Python 绑定；非 Node 集成只能走 `pi --mode rpc`（JSONL over stdio）

## 它的上下文管理（我们照搬的规则）

| 机制 | 源码位置 | 规则 |
|---|---|---|
| 阈值压缩 | `compaction/compaction.ts`（`DEFAULT_COMPACTION_SETTINGS`） | `reserveTokens = 16384`、`keepRecentTokens = 20000`；`shouldCompact()` 在 `contextTokens > contextWindow − reserveTokens` 时触发 |
| token 记账 | `estimateContextTokens()` | 用 provider 报的 usage 作为基准，只对 usage 之后的消息做估算（`usageTokens + trailingTokens`） |
| 裁点选择 | `findValidCutPoints()` / `findCutPoint()` | 裁点必须是合法消息边界：**`toolResult` 明确不是裁点**（不许切在工具调用/结果之间）；切成半轮时记录 `turnStartIndex`、`isSplitTurn` |
| 历史表达 | 会话条目（JSONL） | 上下文就是消息轨迹；压缩产生 `compaction` 条目并链式引用上一个压缩，可持久化、可恢复 |

## 我们借了什么

1. **能力声明只发一次**：原生工具调用模式下工具 schema 由 API `tools` 下发，正文不再重复卡片
   （实测每轮 ~5.8k tokens 的重复）。
2. **历史压成摘要而不是丢弃**：`steps_digest`（一行一步、含参数与关键数值、有行数上限）。
3. **裁点以整步为单位**：摘要不会出现"半条工具结果"。
4. **用量驱动收紧**：上一轮 provider 报的 `prompt_tokens` 超窗口 70% → 收紧档（摘要 8 行、只留最近 1 条）。

## 我们**没有**借（以及为什么）

- **LLM 生成摘要**：Pi 用模型写压缩摘要；我们的循环一步 15–40 s，摘要本身还可能被内容过滤/抖动
  （我们踩过一次空回复把任务打死的坑），所以改用零成本、可测试的**确定性摘要**。
- **会话可恢复/跨会话续跑**：飞行指令非幂等，重放起飞/降落是事故；我们只做记录层恢复
  （把残留 running 标成 interrupted），绝不复跑动作。
- **Node 运行时**：把 Node 进程插进 LLM 与飞控之间会多一跳失败面，且它的默认语义（无审批、无步数上限）
  与飞行安全冲突。

## 可引用的论点

1. "能力/系统提示固定、历史才压缩"是成熟 agent harness 的做法（可引 Pi 的 compaction 设计）。
2. 面向物理执行的 agent 不能照搬"可恢复会话"（非幂等副作用的差异），这是我们的设计取舍点。
