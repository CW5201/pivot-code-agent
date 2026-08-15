# 查询循环，逐阶段解析

`pivotcode/query/loop.py::query_loop` 是一个驱动每个代理轮次（turn）的异步生成器。本页将逐步讲解它在每个阶段做了什么，并附上 file:line 锚点。

前置知识：[concepts/agent-loop.md](../concepts/agent-loop.md) 中的术语（迭代 vs 轮 vs 会话）。

## 入口

```python
async def query_loop(params: QueryParams) -> AsyncGenerator[QueryYield, None]:
    state = LoopState(
        messages=list(params.messages),
        last_input_tokens=params.last_input_tokens_seed,
        last_output_tokens=params.last_output_tokens_seed,
        messages_len_at_last_call=(
            len(params.messages) if params.last_input_tokens_seed > 0 else 0
        ),
    )
    iteration = 0
    while True:
        # phases 1–10 ...
```

`QueryParams` 携带循环所需的一切（消息、供应商、工具、权限回调、中止事件等）。`LoopState` 是每轮可变的内部状态。

## 阶段 1 — 中止检查

```python
if params.abort_event and params.abort_event.is_set():
    yield create_user_interruption_message(tool_use=False)
    return
```

在每次迭代的顶部检查。提示词或工具执行期间的 Ctrl+C 会设置 `abort_event`；我们在这里看到它并退出。会 yield 一条用户中断消息，以便调用方显示「Turn interrupted.」。

## 阶段 1.5 — 注入 system-reminder

```python
yield RequestStartEvent()

injected: list[UserMessage] = []
if iteration == 0:
    for reminder in _build_turn_reminders(params.context):
        injected.append(reminder)
        yield reminder

for queued_msg in _drain_message_queue(params.message_queue):
    injected.append(queued_msg)
    yield queued_msg

if injected:
    state.messages = state.messages + injected
```

- `RequestStartEvent` 向 UI 发出信号：新的 API 调用即将开始（用于触发「Thinking...」指示器）。
- 在本轮的第 0 次迭代，注入一条日期/时间的 `<system-reminder>`——让模型在长时间会话中保持对墙钟时间的感知。
- 排空从其他任务通过 `agent.inject_message(...)` 注入的消息。

所有注入的消息都是 `hide_in_ui=True`——它们会发送给 API，但不会出现在用户的聊天面板中。

## 阶段 2 — 压缩流水线

调用前的守门人。完整的层级讲解参见 [concepts/context-and-compaction.md](../concepts/context-and-compaction.md)。

```python
messages_for_query = get_messages_after_compact_boundary(state.messages)

# Get model info (cached per turn)
if state.cached_model_info is None:
    state.cached_model_info = params.provider.get_model_info(params.model)
threshold_pct = params.settings.get("compaction_threshold_percent", 80) / 100.0
threshold_tokens = int(model_info.context_window * threshold_pct)

# Layer A
messages_for_query = compaction_truncate_tool_results(messages_for_query, ...)

# Layer B
messages_for_query, tokens_saved = compaction_clear_tool_results(messages_for_query, ...)

# Layer C (pre-call estimate via `predicted_next_call_tokens`)
current_tokens = predicted_next_call_tokens(
    params.model, messages_for_query,
    system=params.system_prompt,
    tools=[t.to_schema() ... for t in params.tools],
    last_input_tokens=state.last_input_tokens,
    last_output_tokens=state.last_output_tokens,
    new_messages_since_last_call=(
        state.messages[state.messages_len_at_last_call:]
        if state.last_input_tokens > 0 else None
    ),
)
if current_tokens >= threshold_tokens:
    # fire Layer C
    result = await compaction_auto(...)
    if result:
        state.messages = [result.boundary_message, *result.summary_messages]
        # loop back and retry with summarized history
```

各层按顺序执行；任何一层使总量低于阈值即停止链条。

`predicted_next_call_tokens` 是 `max(usage_based, full_estimate)`，其中：
- `usage_based = last_input_tokens + last_output_tokens + tokens(new_messages_since_last_call)`
- `full_estimate = litellm.token_counter(...)` 或 chars/3 后备方案

取最大值可防止预算低估。

## 阶段 3 — 阻塞上限检查

```python
blocking_limit = model_info.context_window - params.settings.get("blocking_limit_buffer_tokens", 3000)
if current_tokens >= blocking_limit:
    yield create_assistant_error_message(
        "Conversation too long. Please run /compact or start a new session."
    )
    return
```

最后的机会式拒绝。如果即使在压缩后仍距上限 3k token 以内，就干脆不发起 API 调用。轮次干净地结束。

## 阶段 4 — API 调用（流式）

```python
api_messages = normalize_messages_for_api(messages_for_query)
api_messages_dicts = messages_to_openai_dicts(api_messages)

if params.llm_perspective_callback is not None:
    params.llm_perspective_callback(api_messages_dicts, params.system_prompt)

stream = stream_with_retry(
    params.provider.stream,
    api_messages_dicts,
    system=params.system_prompt,
    tools=tool_schemas,
    model=current_model,
    max_tokens=effective_max_output_tokens,
    thinking=...,
    fallback_provider=params.fallback_provider,
)

async for event in stream:
    # dispatch into current_usage / text / tool_use accumulation
```

- `normalize_messages_for_api` 剥离隐藏消息、合并相邻的同角色消息、丢弃孤立的 tool_results。参见 [architecture/messages-and-api.md](messages-and-api.md)。
- `stream_with_retry` 处理可重试错误（429、529、网络），使用指数退避 + 抖动。不可重试的错误（400、401、403）立即向上传播。
- 事件被分派到 `current_usage`（来自 `message_delta`）以及 `TextBlock` / `ThinkingBlock` / `ToolUseBlock` 累加器中。

## 阶段 5 — 响应组装

```python
assistant_msg = AssistantMessage(
    content=assembled_blocks,
    model=current_model,
    stop_reason=stop_reason,
    usage=current_usage,
    ...
)
```

所有流式片段组合成一条 `AssistantMessage`。文本块在流式传输时是 `hide_in_api=True`；最终消息以 `hide_in_api=False` yield 出来——这是调用方存储的最终视图。

## 阶段 6 — Yield + 校准

```python
yield assistant_msg

params.cost_tracker.add_usage(current_usage, current_model)
if current_usage.input_tokens > 0:
    state.last_input_tokens = current_usage.input_tokens
    state.last_output_tokens = current_usage.output_tokens
    state.messages_len_at_last_call = len(state.messages)
```

存储上报的用量，供下一次迭代的调用前估算使用。

## 阶段 7 — 中止与恢复（无工具调用路径）

```python
if params.abort_event and params.abort_event.is_set():
    yield create_user_interruption_message(tool_use=False)
    return

if not tool_use_blocks:
    # Possibly recover from max_output_tokens mid-thought
    if stop_reason == "max_tokens":
        # Escalate from 8k → 64k
        if not state.max_output_tokens_override:
            state.max_output_tokens_override = escalated_max_tokens
            state.transition = "max_output_tokens_escalation"
            continue
        # Multi-turn "Resume directly" recovery
        if state.max_output_tokens_recovery_count < limit:
            state.max_output_tokens_recovery_count += 1
            yield recovery_msg   # "Resume directly..."
            continue

    # Emergency compaction on PTL
    if assistant_msg.api_error == "prompt_too_long" and not state.has_attempted_emergency_compact:
        result = await compaction_auto(...)
        state.messages = [...]
        state.has_attempted_emergency_compact = True
        continue

    # Normal completion
    return
```

多条恢复路径，每条都是一次性的。如果模型在思考中途停止，就升级 token 上限或注入「Resume directly.」。如果提示词过长，就触发紧急压缩。这些在常见情况下都不会用到——它们是安全网。

## 阶段 8 — 工具执行

```python
async for update in run_tools(
    tool_use_blocks, params.tools, params.context,
    max_concurrency=params.settings.get("max_tool_concurrency", 10),
    permission_callback=params.permission_callback,
):
    if update.message:
        yield update.message
        tool_results.append(update.message)
```

`pivotcode/tools/orchestration.py` 中的 `run_tools` 将 `ToolUseBlock` 批量组织为并发任务（只读）或串行任务（写/执行）。每个任务都会产出一条带 `ToolResultBlock` 的 `UserMessage`。

对每个工具，`run_tool_use`（位于 `pivotcode/tools/execution.py`）：

1. 通过 `tool.validate_input(args, ctx)` 校验输入。
2. 触发 pre-tool-use 钩子。
3. 运行权限流水线（`check_permissions`）。
4. 调用 `tool.call(args, ctx)`。
5. 触发 post-tool-use 钩子。
6. 返回包装为 tool_result 消息的 `ToolResult`。

## 阶段 8.5 — 密集模式的记忆提醒

```python
state.turns_since_memory_update += 1
if (
    params.memory_mode == "intensive"
    and state.turns_since_memory_update >= params.settings.get("memory_reminder_threshold", 10)
):
    memory_reminder = create_user_message("<system-reminder>...</system-reminder>", hide_in_ui=True)
    tool_results.append(memory_reminder)
    yield memory_reminder
    state.turns_since_memory_update = 0
```

在密集模式下定期提醒保存记忆。

## 阶段 9 — 最大迭代次数检查

```python
state.iteration_count += 1
if params.max_iterations_per_turn and state.iteration_count >= params.max_iterations_per_turn:
    yield create_attachment_message(
        "max_iterations_per_turn_reached",
        metadata={"max_iterations_per_turn": params.max_iterations_per_turn, "iteration_count": state.iteration_count},
    )
    return
```

防止失控循环的硬性上限。

## 阶段 10 — 下一轮迭代

```python
state.messages = list(messages_for_query) + [assistant_msg, *tool_results]
state.transition = None
state.max_output_tokens_override = None
iteration += 1
# loop
```

组装下一轮迭代的起始状态。`transition` 仅保留用于日志记录。

## 循环在哪里退出

终止条件——循环 `return`：
- 阶段 1 / 7：中止被触发。
- 阶段 3：命中阻塞上限。
- 阶段 7：无工具调用的响应（正常完成）。
- 阶段 9：命中最大迭代次数。

或者，如果出了严重问题，它会向调用方传播异常。

## 相关

- [architecture/overview.md](overview.md) — 循环在整个系统中如何定位。
- [architecture/messages-and-api.md](messages-and-api.md) — 阶段 4 的 `normalize_messages_for_api` 做了什么。
- [concepts/context-and-compaction.md](../concepts/context-and-compaction.md) — 阶段 2 的深入讲解。
- [concepts/tools-and-permissions.md](../concepts/tools-and-permissions.md) — 阶段 8 的深入讲解。