# 消息与 API 载荷

Pivot 的内部 `agent._messages` 列表如何变成线上传输的字节。这正是「用户看到什么」与「模型看到什么」这两个心智模型的分歧点。

## 流水线

```
agent._messages  (raw, includes all history + hidden reminders)
      │
      ▼
boundary slice (drop everything before the last compact summary)
      │
      ▼
compaction layers A → B → C (if over threshold)
      │
      ▼
normalize_messages_for_api  (filter hidden, merge same-role, drop orphan tool_results)
      │
      ▼
messages_to_openai_dicts    (serialize to [{role, content}, ...])
      │
      ▼
provider envelope (Anthropic: convert to Anthropic shape; LiteLLM: pass through)
      │
      ▼
HTTP POST to the provider
```

每一步在下面详解。

## 步骤 1 — 边界切片

`pivotcode/messages/types.py::get_messages_after_compact_boundary` 丢弃最后一条 `SystemMessage(subtype=COMPACT_BOUNDARY)` 之前的所有内容。压缩之后，摘要之前的消息不再发送——由摘要取而代之。

实现：从后向前扫描列表，找到最后一个压缩边界标记，返回从那里开始的所有内容。如果不存在边界（尚未压缩），则返回全部消息。

## 步骤 2 — 压缩各层

详见 [concepts/context-and-compaction.md](../concepts/context-and-compaction.md) 和 [architecture/query-loop.md#phase-2](query-loop.md#phase-2--compaction-pipeline)。

各层可能原地修改消息列表（层 A 截断 tool_result 内容）、返回新列表（层 B 清除旧结果），或用摘要替换整个历史（层 C）。

## 步骤 3 — 归一化

`pivotcode/messages/normalization.py::normalize_messages_for_api`。六个子步骤：

### 3a. 丢弃 ProgressMessage

纯粹用于 UI 进度更新的信息性消息。从不发送。

### 3b. 丢弃 `hide_in_api=True` 的消息

它们存在于 `agent._messages` 中供 UI 重放，但在发送前会被剥离。示例：

- 带日期/时间的 `<system-reminder>`（每轮注入）。
- 关于模型 / 供应商 / 记忆模式变更的 `<system-reminder>`。
- 关于 `/move`、`/convrevert`、`/allrevert` 的 `<system-reminder>`。
- 虚拟的「resume directly」恢复提示。

### 3c. 丢弃 SystemMessage

Pivot 的 `SystemMessage` 类型是**内部**的——`COMPACT_BOUNDARY`、信息性的 `command_output`、错误标记。它们不是 API 层的系统提示词。例外：`local_command` 子类型会被转换为 UserMessage。

### 3d. 把 AttachmentMessage 转换为 UserMessage

`AttachmentMessage` 携带结构化元数据（例如文件 diff 附件、`max_iterations_per_turn_reached` 标记）。转换为 UserMessage，内容字符串化，以便模型能看到。

### 3e. 合并相邻的同角色消息

以下情况必需：
- 许多 LLM API 强制严格的 `user, assistant, user, assistant, ...` 交替（Anthropic、某些 Bedrock 模型）。
- 特别是 Bedrock——拒绝相邻同角色消息。

`merge_user_messages` 和 `_merge_assistant_messages` 会拼接内容列表。

### 3f. 丢弃孤立的 tool_results

合并之后，有可能出现一个 `tool_result` 块没有前置 `tool_use` 的情况（引用的 `tool_use_id` 在对话中已不存在——可能被压缩掉了，也可能被合并掉了）。

`_drop_orphan_tool_results` 遍历合并后的列表，跟踪它看到的每条 assistant 消息上的每个 `tool_use_id`，并剥离 id 不出现在该集合中的 `tool_result` 块。如果丢弃了任何块，会记录一条 WARNING。

没有这一步，API 会以「tool_use_id does not match any tool_use block」返回 400。

## 步骤 4 — 序列化

`pivotcode/messages/serialization.py::messages_to_openai_dicts` 转换为通用的 OpenAI 兼容形态：

```json
[
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": [{"type": "text", "text": "..."}, {"type": "tool_use", "id": "...", ...}]},
  {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "...", "content": "..."}]}
]
```

内容简单时是字符串；存在工具调用、图片或思考块时则是块列表。

## 步骤 5 — 供应商封装

### Anthropic

`AnthropicProvider.stream` 转换为 Anthropic 特有的形态：
- `system` 参数变成缓存作用域的文本块列表（参见 [prompt-caching.md](prompt-caching.md)）。
- `messages` 保持 user/assistant 交替。
- `tool_use` 块使用 Anthropic 的 schema（`{type: "tool_use", id, name, input}`）。
- `tool_result` 块同样（`{type: "tool_result", tool_use_id, content}`）。
- 请求头：`anthropic-version`，以及用于缓存 / 思考 / 缓存键功能的 `anthropic-beta`。

### LiteLLM

`LiteLLMProvider.stream` 把 OpenAI 字典透传给 `litellm.acompletion(...)`，后者在内部处理供应商特定的重整形。我们的职责只是确保字典格式良好。

设置了 `stream_options={"include_usage": True}`，这样 `usage` 会出现在最后一个流块中——这是我们的 token 核算所必需的。

### Scripted

`ScriptedProvider` 完全忽略载荷，直接返回第 N 个预置响应。

## 两条路线的分歧——用户所见 vs API 所见

| 项目 | 出现在用户聊天面板中？ | 发送给 API？ |
|---|---|---|
| 用户输入的提示词 | ✅ | ✅ |
| Assistant 文本（流式） | ✅ | ✅ |
| 工具调用块 | ✅ | ✅ |
| 工具结果面板 | ✅ | ✅ |
| 日期/时间的 `<system-reminder>` | ❌（hide_in_ui） | ✅ |
| `/move` / `/revert` 之后的 `<system-reminder>` | ❌ | ✅ |
| `ProgressMessage`（压缩已开始） | ✅ 作为信息行 | ❌ |
| `SystemMessage(COMPACT_BOUNDARY)` | ✅ 作为细微标记 | ❌（步骤 3c 过滤） |
| `AttachmentMessage(max_iterations_per_turn_reached)` | 取决于 UI | ✅（转换为 UserMessage） |
| 层 B 清除的工具结果 | ✅（直到下次发送仍显示原文） | ❌（以 `[cleared to free context]` 发送） |

## 调试 — LLM 视角面板

GUI 的 LLM 视角面板显示序列化后的消息（步骤 4 的输出）以及系统提示词。这是「该轮模型实际看到了什么」的权威视图。

在 Python 中，同样的数据可通过 `llm_perspective_callback` 获得：

```python
def on_perspective(messages_dicts, system_prompt):
    print(json.dumps(messages_dicts, indent=2))

agent = PivotCodeAgent(...)
agent._llm_perspective_callback = on_perspective
```

每次 API 调用前调用。

## 相关

- [architecture/query-loop.md](query-loop.md) — 归一化发生在哪里（阶段 4）。
- [architecture/system-prompt.md](system-prompt.md) — 载荷的系统提示词半部分如何构建。
- [concepts/context-and-compaction.md](../concepts/context-and-compaction.md) — 压缩如何重塑消息列表。
- `pivotcode/messages/` — 实现。