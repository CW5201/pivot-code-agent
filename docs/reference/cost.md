# 成本与 Token 跟踪

每次代理回合结束后，Pivot Code 会在响应下方打印一行摘要：

```
  Session: 16,378 in + 53 out = $0.0050 (estimated) | Conversation: 16,356 / 1,048,576 (1%)
```

## 每个字段的含义

- **会话（输入 / 输出）** — 自会话开始以来的输入和输出 Token 总数（不只是最后一回合）。跨回合持续累积，在 `/clear` 或新会话时清零。
- **`= $…`（估算）** — 基于 LiteLLM 定价注册表的尽力而为的美元估算。当模型不在注册表中时显示 `unknown`（本地模型、新发布的模型和微调模型很常见）。
- **对话** — 当前对话的 Token 大小与模型上下文窗口的对比，带百分比。每回合更新；一旦达到配置的压缩阈值（`compaction_threshold_percent`，默认 80%），Pivot 就会开始压缩。

## 更详细的分解 — `/status`

`/status` 命令显示完整的账目明细：

| 行 | 说明 |
|---|---|
| `Input tokens` | 发送给模型的未缓存输入。 |
| `Cache creation tokens` | 本会话中写入提示词缓存的 Token。在 Anthropic 上计费高于普通输入；每个缓存条目只计一次。 |
| `Cache read tokens` | 从提示词缓存中读取的 Token。计费约为普通输入的 10%——这就是提示词缓存的价值所在。注意：即使缓存实际发生，也可能不会显示。 |
| `Total input` | 以上三者的总和。这就是单行摘要中"in"所指的内容。 |
| `Output tokens` | 会话期间模型生成的输出 Token。 |
| `Estimated cost` | 美元估算值。如果模型的价格未注册，则显示 `unknown`。 |

## 为什么是"估算"

Pivot Code 在客户端根据 Token 数量 × 注册的每 Token 价格计算成本。

- 数字接近但不具有权威性。请以供应商仪表盘上的确切数据为准。
- 对于 LiteLLM 未定价的模型（本地模型、新发布的模型、微调模型），成本为 unknown。
- 当供应商在响应中返回缓存元数据时，会应用缓存定价计算。Anthropic 供应商能准确报告缓存 Token。LiteLLM 的流式模式可能无法传播某些供应商（例如 OpenRouter）的缓存 Token 明细——在这种情况下，即使缓存处于启用状态且供应商侧账单已应用节省，`/status` 仍会显示缓存 Token 为零。

## 调整预算

影响成本行为的关键设置（参见 [`cli.md`](cli.md)）：

- `max_iterations_per_turn` — 单条用户消息可消耗的 API 调用次数硬上限。
- `max_output_tokens` — 每次调用的输出上限，当模型达到限制需要恢复时，内部最多升级至 `escalated_max_tokens`。
- `compaction_threshold_percent` — 上下文窗口达到多大比例时 Pivot 开始压缩，以避免触及硬上限（触及后调用会被拒绝）。
- `auto_compact_buffer_tokens` — 距上下文窗口上限多少余量时触发自动压缩。

## 编程访问

通过 `PivotCodeAgent`：

```python
agent.cost_usd       # float — session cost in USD
agent.cost_unknown   # bool  — True when pricing isn't available
agent.usage          # Usage — dataclass with input/output/cache breakdown
```

完整的结构请参见 `pivotcode/messages/types.py → Usage`。