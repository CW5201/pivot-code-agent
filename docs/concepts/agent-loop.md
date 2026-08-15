# 智能体循环

Pivot Code 的核心是一个驱动每次对话的异步生成器：`pivotcode/query/loop.py` 中的 `query_loop()`。理解它的结构——以及 Pivot 围绕它使用的术语——能让系统的其余部分更容易推理。

## 术语

三个含义精确的术语：

| 术语 | 定义 |
|---|---|
| **迭代** | 对 `query_loop` 的 while 循环的一次遍历——一次 API 调用，可选地后跟工具执行。 |
| **轮** | 两次用户输入之间发生的一切。一轮包含 1 次及以上迭代，直到智能体停止并再次等待输入。 |
| **会话** | 从开始到 `/clear` 或进程退出的完整对话。持久化在磁盘上；可以用 `--resume` 恢复。 |

因此：

- 用户说「修复这个 bug」→ 这开始了一**轮**。
- 在这一轮中，Pivot 可能运行多次**迭代**：调用 LLM → 得到 `tool_use` → 运行工具 → 带着结果再次调用 LLM → ... → 最终文本回复。
- 跨轮的整个对话历史就是**会话**。

正是这套术语解释了为什么 `max_iterations_per_turn`（原先叫 `max_turns` 的设置）会这样命名——它限制的是一条用户消息能触发的 API 调用次数，而不是一个会话能有多少条用户消息。

## 循环结构

每次迭代都要经过 10 个阶段。简化的伪代码：

```
while True:
    # 1. Check abort (Ctrl+C)
    # 2. Inject turn-start reminders (date/time), drain queued messages
    # 3. Compaction pre-check:
    #       - Layer A: truncate oversized tool results
    #       - Layer B: clear old tool results
    #       - Layer C: auto-compact if still above threshold
    # 4. Blocking-limit check (refuse call if too close to ceiling)
    # 5. API call (streaming)
    # 6. Process response — collect content blocks + tool_use blocks
    # 7. Handle no-tool-use responses (completion or recovery)
    # 8. Execute tools (concurrent for read-only, serial for writes)
    # 9. Check max_iterations_per_turn
    # 10. Loop back
```

每个阶段都小而局部。完整的逐阶段讲解（带 file:line 指针）见 [architecture/query-loop.md](../architecture/query-loop.md)。

## 什么会结束一轮

一轮在以下情况发生时结束：
- 模型返回纯文本响应，没有工具调用。
- 用户按下 Ctrl+C（干净中止）。
- 达到 `max_iterations_per_turn`。
- 遇到阻塞性错误（尽管压缩仍发生上下文溢出、反复触及输出 token 上限等）。

当这些情况中的任何一种发生时，控制权会返回给 REPL，它打印该轮的成本摘要并等待下一条用户输入。

## 流式输出

每次 API 调用都是流式的。你会看到：
- 逐 token 的文本（CLI 上的 Rich 实时打印，GUI 中的 WebSocket 事件）。
- 支持该功能的模型的增量「思考」块。
- 工具调用块在到达时立即以带框面板渲染。

流由 `provider.stream(...)` 驱动，它产生结构化事件：`StreamTextDelta`、`StreamToolUseStart`、`StreamToolUseStop` 等。循环消费这些事件，将它们组装成消息，并将结果交给调用方。

## 错误恢复

循环透明处理的三种错误：

1. **思考中途触及输出 token 上限**：助手被截断。循环将 `max_tokens` 从 8k 提升到 64k 并重试。如果仍然被截断，注入「直接继续，无需道歉，从中断的思考处接上」，最多 3 次。
2. **提示过长（413）**：触发紧急压缩，并使用摘要后的历史重新运行。
3. **可重试的网络错误（限流、超时、529）**：在 `pivotcode/api/retry.py` 中通过指数退避处理。

不可重试的错误（400、401、403）会立即传播——它们是用户可操作的（密钥错误、请求形态错误）。

## 中止处理

随时按 Ctrl+C：
- 设置一个 `asyncio.Event`，循环在第 1 阶段和第 7 阶段检查它。
- 导致 `ask_user_callback` 抛出 `CancelledError`，它会穿过工具执行层传播。
- REPL 捕获它，打印「轮次已中断。」，清除中止标志，并等待新的输入。

会话的 `_last_usage` 和 `turn_count` 仍然通过智能体 `finally` 中的尽力而为块刷新到磁盘，因此记账能在中断后存活。

## 状态管理

在迭代之间，循环携带一个 `LoopState`（`pivotcode/query/state.py`）：

- `messages` —— 完整消息列表。
- `iteration_count` —— 本轮已进行的 API 调用次数。
- `max_output_tokens_recovery_count` —— 用于 8k→64k 的升级。
- `has_attempted_emergency_compact` —— 每轮一次性。
- `last_input_tokens` / `last_output_tokens` —— 用于调用前的压缩估算。
- `cached_model_info` —— 避免每次迭代都重新向供应商查询上下文窗口。

一轮结束时，`LoopState` 会被丢弃。持久状态是智能体上的 `self._messages` 和磁盘上的 `SessionState`。

## 相关阅读

- [reference/settings.md](../reference/settings.md) —— 调整压缩阈值、最大迭代数、重试预算。
- [concepts/context-and-compaction.md](context-and-compaction.md) —— 三个压缩层的详细说明。
- [concepts/tools-and-permissions.md](tools-and-permissions.md) —— 工具执行在第 8 阶段内部实际如何发生。
- [architecture/query-loop.md](../architecture/query-loop.md) —— 面向贡献者的逐阶段代码讲解。