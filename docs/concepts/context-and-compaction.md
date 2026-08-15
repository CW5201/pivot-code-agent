# 上下文与压缩

长会话会耗尽上下文。模型的上下文窗口是固定的——Claude Sonnet 4 有 200k token，GPT-4o 有 128k，Gemini 2.5 有 1M。如果不加干预，密集的调试会话会在 15–30 轮内填满窗口。

Pivot Code 通过一个三层压缩管线解决这个问题，该管线在**每次 API 调用之前**运行，只在需要时渐进地释放空间。你几乎不需要考虑它——但当你确实需要时，它的工作原理如下。

## 每轮之后你会看到的一行摘要

```
Session: 8,118 in + 153 out = $0.0082 (estimated) | Conversation: 8,271 / 200,000 (4%)
```

- **会话** —— 自会话开始以来的累计 token 与花费。
- **对话** —— 上下文窗口**当前**的占用程度。当接近 80 % 时，压缩会在下一次调用时触发。

## 四个同心阈值

Pivot 使用四个同心 token 阈值，每个都有自己的反应：

| 阈值 | 触发条件 | 效果 |
|---|---|---|
| **警告阈值**（`context_window - 20k`） | 向用户显示「即将占满」信号。 | 仅提示。 |
| **自动压缩阈值**（`context_window * 80 %`） | 依次运行层 A → B → C，直到重新低于阈值。 | 压缩发生在*下一次*调用前检查时。 |
| **压缩缓冲区**（`context_window - 13k`） | 循环内的紧急压缩。 | 在 413 响应上做最后一次摘要。 |
| **阻塞上限**（`context_window - 3k`） | 直接拒绝 API 调用。 | 轮次以「对话过长。请运行 /compact 或开始新会话。」结束。 |

所有四个阈值都可以通过设置调整——见 [reference/settings.md](../reference/settings.md)。

## 三个压缩层

每次迭代，如果预测的调用前 token 数超过阈值，各层依次运行。任何将我们带到阈值以下的层都会终止该链条。

### 层 A——截断过大的工具结果

`pivotcode/compact/compact_truncate.py`

重写内容超过 `tool_result_max_chars`（默认 20 000 字符）的单个 `tool_result` 块。该块会被替换为：

```
[ALAN-TRUNCATED] Tool result truncated — 216000 chars exceeded 20000 limit.
```

`[ALAN-TRUNCATED]` 哨兵让后续的压缩过程（以及调试）能区分合成内容与真实内容。消息的结构得以保留（它仍是具有相同 `tool_use_id` 的 `tool_result`），因此对话形态保持完整。

**何时有帮助**：单个臃肿的工具输出（例如对 500 KB 日志执行 `cat`）主导了上下文。该层只削减那一个块，不触碰周围的消息。

### 层 B——清除旧的工具结果

`pivotcode/compact/compact_clear.py`

将较旧 `tool_result` 块的**内容**替换为短哨兵，只保留最近的 N 个（默认 `compact_clear_keep_recent = 10`）。模型仍能看到调用了某个工具，但输出被缩减为：

```
[cleared to free context space]
```

**何时有帮助**：智能体调用了 50 次 `Read`；每次结果都很小，但合在一起就主导了上下文。该层能压平长尾。

### 层 C——自动压缩（分叉摘要器）

`pivotcode/compact/compact_auto.py`

主力。如果在 A 和 B 之后仍超过阈值：

1. 分叉一个**独立**的 LLM 调用，**不带工具**，并使用特定的摘要提示（`pivotcode/compact/prompt.py` 中的 9 节模板）。
2. 该调用产生一个 `<analysis>…</analysis><summary>…</summary>` 响应。
3. 摘要替换压缩前的历史。插入一个 `SystemMessage(subtype=COMPACT_BOUNDARY)` 标记，以便后续压缩知道截止点在哪里。
4. 注入一条压缩后的用户消息：*「本会话是从一个耗尽上下文的先前对话继续的。请从它中断的地方继续，不要提问。」*

**何时有帮助**：对话中有大量来回内容，任何机械截断都无法压缩。摘要捕获了意图、关键决策、待办任务以及精确的当前状态。

## 紧急路径

如果 API 调用在调用前检查之后**仍然**以 `prompt too long`（413 路径）失败：

1. 流错误处理器捕获 PTL 信号。
2. 以紧急压缩的方式同步运行层 C。
3. 使用摘要后的历史重试调用。

这是一项双重保险措施——实践中很少见，但对可靠性至关重要。

## 手动压缩

```
> /compact
```

按需运行层 C，无论你是否接近阈值。适合在会话中途切换模型之前（更小的上下文窗口），或想主动浓缩一次漫无边际的探索时使用。

```
> /compact focus on the bug we just fixed, not the earlier refactoring
```

`/compact` 之后的任何文本都会作为*「附加说明」*追加到摘要提示中，用于引导强调的重点。

## 断路器

如果层 C 连续失败三次（`max_consecutive_compact_failures = 3`），断路器触发，Pivot 会显示：

```
Compaction has failed 3 times consecutively. Use /clear to start fresh.
```

三次失败强烈表明存在对抗性状态（token 计数偏差数万、摘要提示让模型困惑等）。Pivot 不会在循环中烧钱，而是退出。

## 调优

`.pivot/settings.json` 中的设置（或运行时通过 `/settings <key> <value>`）：

| 设置 | 默认值 | 作用 |
|---|---|---|
| `compaction_threshold_percent` | 80 | 自动压缩触发的时机，按上下文窗口百分比计。 |
| `tool_result_max_chars` | 20 000 | 层 A 的单个工具结果大小上限。 |
| `compact_clear_keep_recent` | 10 | 层 B 的「保留最近 N 个」数量。 |
| `compact_max_output_tokens` | 20 000 | 层 C 摘要调用的输出预算。 |
| `auto_compact_buffer_tokens` | 13 000 | 距上限多近时触发紧急压缩。 |
| `blocking_limit_buffer_tokens` | 3 000 | 硬性下限——低于此值则拒绝调用。 |
| `max_consecutive_compact_failures` | 3 | 断路器阈值。 |

## 检查发生了什么

在 GUI 中，**LLM 视角**面板会显示每次调用发送的精确载荷，包括任何作为用户消息注入的压缩后摘要。当你想了解 Pivot 记住了什么、什么被压缩掉了时，这是最好的调试视图。

在 CLI 中，`/status` 显示当前的 `Conversation` token 数，磁盘上的会话记录（`.pivot/sessions/<id>/transcript.jsonl`）记录每一条消息，包括压缩边界。

## 相关

- [concepts/agent-loop.md](agent-loop.md) —— 压缩在循环中的运行位置。
- [reference/settings.md](../reference/settings.md) —— 所有调优旋钮。
- [reference/cost.md](../reference/cost.md) —— 状态行数字的含义。
- [architecture/query-loop.md](../architecture/query-loop.md) —— 循环的第 2 阶段是压缩管线。