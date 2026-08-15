# Python API 参考

主要的公共类是 `pivotcode.PivotCodeAgent`。本页介绍你可能会用到的方法和属性。

教程式入门请参阅 [guides/building-agents.md](../guides/building-agents.md)。

## 构造函数

```python
from pivotcode import PivotCodeAgent

PivotCodeAgent(
    *,
    cwd: str | None = None,
    provider: str | LLMProvider = "litellm",  # or "anthropic"
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    permission_mode: str | None = None,
    max_iterations_per_turn: int | None = None,
    max_output_tokens: int | None = None,
    memory: str | None = None,
    tool_call_format: str | None = None,
    session_id: str | None = None,
    ask_callback: Callable | None = None,
    verbose: bool = False,
    extra_tools: list[Tool] | None = None,
    custom_system_prompt: str | None = None,
    gui_label: str | None = None,
    programmatic: bool = False,
    tools: list[Tool] | None = None,
    disabled_tools: list[str] | None = None,
    **provider_kwargs: Any,
)
```

所有省略的设置（`None`）都会回退到 `.pivot/settings.json` → 内置默认值。参见 [guides/configuration.md](../guides/configuration.md)。

关键参数：

- **`cwd`** — 代理运行的工作目录。默认为 `os.getcwd()`。
- **`provider`** — 可以是字符串（`"anthropic"`、`"litellm"`、`"scripted"`），也可以是具体的 `LLMProvider` 实例（允许你注入自定义供应商）。
- **`session_id`** — 如果设置，则恢复已有会话；否则生成新的会话 ID。
- **`ask_callback`** — `async def callback(question: str, options: list[str]) -> str`。当工具需要用户批准时被调用。返回所选选项的文本（或任意字符串作为自由文本答案）。
- **`extra_tools`** — 追加到代理工具列表的额外工具。嵌入模式请参阅 [guides/building-agents.md](../guides/building-agents.md)。
- **`custom_system_prompt`** — 设置后，完全替换 Pivot 的默认系统提示词部分。
- **`gui_label`** — GUI 桥接的 URL 路径段。默认为 cwd 的 basename。
- **`programmatic`** — 当为 `True` 时，将 Pivot 作为库组件而非开发者助手运行。参见下方的 [编程模式](#programmatic-mode)。
- **`tools`** — 显式的基础工具列表，替换默认的内置工具。与 `disabled_tools` 和 `extra_tools` 组合使用。参见下方的 [工具选择](#tool-selection)。
- **`disabled_tools`** — 要从基础集中移除的工具名称列表（例如 `["WebFetch", "GitCommit"]`）。

## 查询方法

2×2 矩阵：

|  | 同步 | 异步 |
|---|---|---|
| **仅最终文本** | `query(prompt) -> str` | `query_async(prompt) -> str` |
| **流式事件** | `query_events(prompt) -> list[Event]` | `query_events_async(prompt) -> AsyncGenerator[Event]` |

### `query(prompt: str) -> str`

同步运行一个回合。返回助手的最终文本响应。

```python
answer = agent.query("Explain the compaction system")
```

内部运行 `asyncio.run`；如果事件循环已在运行，则分派到工作线程（Jupyter 安全）。

### `async query_async(prompt: str) -> str`

与 `query` 相同，但可等待。

```python
answer = await agent.query_async("Explain the compaction system")
```

### `query_events(prompt: str) -> list[Event]`

同步方法；回合完成后返回完整的事件列表。适合事后检查。

### `async query_events_async(prompt: str) -> AsyncGenerator[Event, None]`

真正的基础方法。事件产生时逐一产出：

```python
async for event in agent.query_events_async("Summarize README.md"):
    # handle each event
    pass
```

事件是来自 `pivotcode.messages.types` 的消息数据类：

| 事件 | 时机 |
|---|---|
| `RequestStartEvent` | 每次 API 调用开始时（可用于"思考中..."指示器）。 |
| `AssistantMessage` with `hide_in_api=True` | 流式增量——文本块、思考块。 |
| `AssistantMessage` with `hide_in_api=False` | 流结束后组装完成的最终消息。包含工具调用。 |
| `UserMessage` | 注入的消息（系统提醒、工具结果）。 |
| `SystemMessage` | 信息性消息（压缩标记等）。 |
| `AttachmentMessage` | 结构化元数据（例如 `max_iterations_per_turn_reached`）。 |
| `ProgressMessage` | 长时间运行操作的进度更新。 |

通过 `hide_in_api` 过滤来区分流式增量与最终消息——参见 [guides/building-agents.md](../guides/building-agents.md) 中的流式示例。

## 状态检查

| 属性 | 类型 | 说明 |
|---|---|---|
| `agent.session_id` | `str` | 当前会话 ID（自动生成或传入）。 |
| `agent.messages` | `list[Message]` | 当前对话的副本（可以安全地修改返回的列表）。 |
| `agent.usage` | `Usage` | 整个会话的累计 Token。 |
| `agent.last_usage` | `Usage` | 最近一次成功 API 调用的用量。 |
| `agent.cost_usd` | `float` | 累计估算成本。 |
| `agent.cost_unknown` | `bool` | 如果模型的价格未知则为 `True`。 |
| `agent.cwd` | `str` | 工作目录。 |
| `agent.turn_count` | `int` | 本会话已处理的用户消息数量。 |

`Usage` 包含：`input_tokens`、`output_tokens`、`cache_read_input_tokens`、`cache_creation_input_tokens`，以及一个对三种输入类型求和的 `total_input` 属性。

## 运行时控制

### `abort()`

```python
agent.abort()
```

设置中止事件。运行中回合的下一个 `await` 检查点会捕获该事件并干净地展开回退。

### `inject_message(text: str)`

```python
agent.inject_message("Actually, focus on calc.py only.")
```

将一条用户消息排入队列，在下一轮迭代开始时投递。适合在回合中途进行引导的编排框架。

### `update_session_setting(key: str, value: Any) -> str | None`

```python
error = agent.update_session_setting("permission_mode", "yolo")
if error:
    print("Invalid:", error)
```

校验并更新设置（内存 + 磁盘）。校验失败时返回错误消息字符串，成功时返回 `None`。与供应商相关的设置会触发供应商重建。

## 生命周期

### `async close()`

```python
await agent.close()
```

触发 `session_end` 钩子。完成后调用一次。CLI 在 `/exit` 时会这样做。

## 编程模式

当 Pivot 由另一个程序驱动（基准测试框架、父代理、自动化流水线）而非终端前的开发者时，使用 `programmatic=True`。它会让 Pivot 脱离项目和主机级别的状态——这些状态通常对交互式助手有帮助，但会污染受控运行。

```python
agent = PivotCodeAgent(
    model="claude-sonnet-4-6",
    cwd="/path/to/experiment",
    permission_mode="yolo",
    programmatic=True,
)
```

当 `programmatic=True` 时：

- `~/.pivot/PIVOT.md`（全局指令）**不会**被加载。
- `<cwd>/PIVOT.md`（项目指令）**不会**被加载。
- `~/.pivot/memory/MEMORY.md`（全局记忆索引）**不会**被加载。
- 跳过 AGT（Agentic Git Tree）引导——不创建 HEAD 快照，不修改 `.gitignore`。
- 默认工具集不包含 `WebFetch`、`GitCommit` 和 `AskUserQuestion`。也不会追加 `SkillTool`。

`<cwd>/.pivot/sessions/<id>/` 中的项目级状态（对话记录、状态、scratchpad）保持不变——那是代理自身的工作记忆，恢复会话时需要用到。

你可以用 `tools=` 覆盖精选工具集，或用 `disabled_tools=` 对其进行调整（见下文）。

## 工具选择

三个开关控制代理的工具列表，按顺序应用：

1. **基础集。** 按以下顺序取第一个：
   - 如果传入了 `tools=[...]`（显式替换），
   - 如果 `programmatic=True` 则使用精选的程序化工具集，
   - 否则使用所有已启用的内置工具（此时会追加 `SkillTool`）。
2. **减去** `disabled_tools` 中列出的任何名称。
3. **追加** `extra_tools` 中的任何内容。

```python
# Read-only assistant: drop write/exec tools entirely
agent = PivotCodeAgent(disabled_tools=["Bash", "Edit", "Write", "GitCommit"])

# Custom tool list (e.g. for a domain-specific agent)
agent = PivotCodeAgent(tools=[MyDomainTool(), MyOtherTool()])

# Programmatic mode plus an extra custom tool
agent = PivotCodeAgent(programmatic=True, extra_tools=[MyTool()])
```

## 会话锁定

`SessionState` 在构造时会对 `<cwd>/.pivot/sessions/<session_id>/session.lock` 获取排他 `flock`。第二个进程尝试打开同一会话时会抛出 `pivotcode.session.SessionLockedError`。锁会在 `agent.close()` 和进程退出时释放。

## 自定义权限回调

```python
async def my_ask(question: str, options: list[str]) -> str:
    print(f"\n{question}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    choice = input("> ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(options):
        return options[int(choice) - 1]
    return choice  # free-text answer

agent = PivotCodeAgent(ask_callback=my_ask, permission_mode="edit")
```

当工具需要批准时会等待回调。返回其中一个选项字符串以接受相应操作（Allow、Deny、Allow always），或返回任何其他字符串——该字符串将成为发回模型的"工具结果"（这样用户一步就能带理由拒绝）。

在回调内按 Ctrl+C 应抛出 `KeyboardInterrupt` → Pivot 会将其转换为 `asyncio.CancelledError` → 回合干净地中止。

## 示例：一个最小的同步脚本

```python
from pivotcode import PivotCodeAgent

agent = PivotCodeAgent(
    model="openrouter/google/gemini-2.5-flash",
    permission_mode="yolo",  # auto-approve for automation
)

answer = agent.query("What's 2+2?")
print(answer)

print(f"Cost: ${agent.cost_usd:.4f}")
print(f"Tokens: {agent.usage.total_input} in, {agent.usage.output_tokens} out")
```

## 示例：异步流式

```python
import asyncio
from pivotcode import PivotCodeAgent
from pivotcode.messages.types import AssistantMessage, TextBlock, ToolUseBlock

async def main():
    agent = PivotCodeAgent(permission_mode="yolo")
    async for event in agent.query_events_async("List files and summarize."):
        if not isinstance(event, AssistantMessage):
            continue
        for block in event.content:
            if event.hide_in_api and isinstance(block, TextBlock):
                print(block.text, end="", flush=True)
            elif not event.hide_in_api and isinstance(block, ToolUseBlock):
                print(f"\n[tool: {block.name}({block.input})]")

asyncio.run(main())
```

## 示例：注入自定义后端

```python
from pivotcode import PivotCodeAgent
from pivotcode.providers.base import LLMProvider

class MyBackend(LLMProvider):
    async def stream(self, messages, system, tools, *, model, max_tokens, thinking, **kwargs):
        # yield StreamEvent objects
        ...
    def get_model_info(self, model):
        ...

agent = PivotCodeAgent(backend=MyBackend(...))
```

## 远程 scripted 后端

`PivotCodeAgent(backend="scripted", model="remote", ...)` 会启动一个内嵌 HTTP 服务器，等待外部调用方（人类或另一个代理）充当 LLM。适合在不消耗 Token 的情况下调试工具接线、系统提示词和框架集成。

端点、载荷格式以及典型的 curl 循环请参见 [guides/remote-scripted-backend.md](../guides/remote-scripted-backend.md)。

## 相关

- [guides/building-agents.md](../guides/building-agents.md) — 教程式入门。
- [guides/remote-scripted-backend.md](../guides/remote-scripted-backend.md) — HTTP 驱动的模拟后端。
- [reference/tools.md](tools.md) — 代理可以访问哪些工具。
- [reference/settings.md](settings.md) — 哪些 kwargs 有效以及适用哪些默认值。
- [architecture/query-loop.md](../architecture/query-loop.md) — 每次 `query_events_async` 调用内部发生了什么。