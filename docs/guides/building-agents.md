# 将 agent 作为库来构建

Pivot Code 在命令行上所做的一切都由 `PivotCodeAgent` 类驱动。你可以直接从 Python 使用它来构建自己的编码 agent、编排器、自动修复循环和自定义界面。

## 最小用例

三行代码即可得到一个可用的 agent：

```python
from pivotcode import PivotCodeAgent

agent = PivotCodeAgent()
print(agent.query("What does this project do?"))
```

这与 CLI 上的 `pivotcode` 行为完全一致——相同的工具、相同的权限管道（默认 `edit` 模式，因此写入需要 stdin 批准）、相同的压缩。

## 四种查询 API

`PivotCodeAgent` 暴露了一个 2×2 矩阵。选择与你的调用方匹配的那一个：

|  | 同步 | 异步 |
|---|---|---|
| **仅最终文本** | `query(prompt) -> str` | `query_async(prompt) -> str`（可等待） |
| **全部事件（流式）** | `query_events(prompt) -> list[Event]` | `query_events_async(prompt) -> AsyncGenerator[Event]` |

只有 `query_events_async` 真正干活——其余的都是围绕它的轻量适配器。

### 同步，最终文本

```python
answer = agent.query("Fix the bug in calc.py")
```

阻塞直到本轮完成。返回助手的最终文本响应。底层机制：如果事件循环已在运行，则在工作线程中运行 `asyncio.run`（Jupyter 安全）。

### 异步，最终文本

```python
import asyncio

async def main():
    agent = PivotCodeAgent()
    answer = await agent.query_async("Fix the bug in calc.py")

asyncio.run(main())
```

在异步场景中使用（FastAPI 端点、异步 worker 等）。

### 流式事件

用于实时渲染、进度条、自定义 UI：

```python
import asyncio
from pivotcode import PivotCodeAgent
from pivotcode.messages.types import AssistantMessage, TextBlock, ToolUseBlock

async def main():
    agent = PivotCodeAgent(permission_mode="yolo")
    async for event in agent.query_events_async("List files and summarise."):
        if not isinstance(event, AssistantMessage):
            continue
        for block in event.content:
            if event.hide_in_api and isinstance(block, TextBlock):
                # Streaming delta — print as it arrives
                print(block.text, end="", flush=True)
            elif not event.hide_in_api and isinstance(block, ToolUseBlock):
                # Final message — tool call block
                print(f"\n[tool: {block.name}({block.input})]")

asyncio.run(main())
```

关键概念：`AssistantMessage` 每轮迭代到达**两次**——一次是流式增量（`hide_in_api=True`，文本块），一次是最终组装好的消息（`hide_in_api=False`，含工具调用）。按 `hide_in_api` 过滤可避免重复输出。

## 仓库中的完整示例

examples 目录中有三个可直接运行的脚本：

- [`examples/example_1_cli_agent.py`](../../examples/example_1_cli_agent.py) —— 10 行交互式 CLI 循环。
- [`examples/example_2_auto_fix_loop/`](../../examples/example_2_auto_fix_loop/) —— 迭代 `agent.query()` + `pytest`，直到测试通过。
- [`examples/example_3_streaming_agent.py`](../../examples/example_3_streaming_agent.py) —— 用于自定义 UI 的异步流式。

每个脚本既可以针对真实 LLM 运行，也可以针对 `ScriptedProvider`（无需 API）运行，以实现确定性测试。

## 配置

把任何你想在 `settings.json` 中设置的项作为构造函数 kwarg 传入：

```python
agent = PivotCodeAgent(
    model="openrouter/google/gemini-2.5-flash",
    permission_mode="yolo",
    max_iterations_per_turn=15,
    max_output_tokens=16_000,
    memory="off",
    cwd="/path/to/project",
    session_id=None,     # None = new session
    api_key=None,        # None = from env
    verbose=False,
    ask_callback=None,   # Custom permission prompt; see below
)
```

传输后端由 `model` 推断——只有需要覆盖推断结果时才传入 `backend="anthropic-native" | "auto" | "scripted"`（或一个 `LLMProvider` 实例）。

省略的 kwarg 会沿优先级链回退（会话 → 项目 settings.json → 默认值）。参见 [guides/configuration.md](configuration.md)。

## 自定义权限提示

默认情况下，`PivotCodeAgent` 没有 `ask_callback`——因此需要批准的工具有时会直接被拒绝。提供你自己的回调即可将权限提示集成到你的 UI 中：

```python
async def ask(question: str, options: list[str]) -> str:
    """Return the chosen option (or 'Other' + free text)."""
    # Your custom dialog / API call / whatever.
    print(f"\n{question}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    choice = input("> ").strip()
    try:
        return options[int(choice) - 1]
    except (ValueError, IndexError):
        return choice  # treat as free-text answer

agent = PivotCodeAgent(ask_callback=ask, permission_mode="edit")
```

回调签名是 `async def ask(question: str, options: list[str]) -> str`。返回选中的选项文本，或返回任意其他字符串作为「工具结果」回传（这样用户就能用自己的理由拒绝）。

## 会话持久化

每一轮，消息都会持久化到 `<cwd>/.pivot/sessions/<session_id>/transcript.jsonl`。要恢复：

```python
agent = PivotCodeAgent(session_id="a1b2c3...")
```

会话状态（成本总计、允许规则、agent 位置、上次使用情况）会一并恢复。

## 成本与 token

```python
agent.usage.input_tokens       # cumulative
agent.usage.output_tokens
agent.cost_usd                 # estimated $ total
agent.cost_unknown             # True if pricing isn't available

agent.last_usage.input_tokens  # most recent call only
```

`usage`（累计）和 `last_usage`（最近一次）都是 `Usage` 数据类，包含完整的细分：输入、输出、缓存创建、缓存读取。

## Scripted provider —— 确定性测试

用于不希望真实 API 调用的测试和 CI：

```python
from pivotcode.providers.scripted_provider import (
    ScriptedProvider, text, tool_call, multi_tool_call,
)

provider = ScriptedProvider.from_responses([
    multi_tool_call(
        ("Bash", {"command": "ls"}),
        ("Read", {"file_path": "/etc/hostname"}),
    ),
    text("Done. The system is ..."),
])

agent = PivotCodeAgent(backend=provider, permission_mode="yolo")
answer = agent.query("check the system")
```

列表中的每个条目就是后端在第 N 轮迭代时返回的内容。零网络、零成本、完全确定。

## 运行中途注入消息

很少需要但偶尔有用——在 agent 思考时向它发送消息：

```python
agent.inject_message("Actually, focus on calc.py only.")
```

消息会被排队，并在下一轮迭代开始时送达。对于需要中途转向的编排框架很方便。

## 中止

```python
agent.abort()
```

设置中止事件。下一个 `await` 检查点会捕获它并干净地展开本轮。图形界面的「Stop」按钮和 CLI 中的 Ctrl+C 都使用它。

## 生命周期

```python
agent = PivotCodeAgent(...)           # sync init; loads session state if session_id given
try:
    agent.query("...")
    agent.query("...")
finally:
    agent.close()                    # async: fires session-end hooks
```

`agent.close()` 是异步的。CLI 会在 `/exit` 时替你调用它。库使用者应该自己调用它，或使用上下文管理器（尚未提供；TODO）。

## 编程模式

当 Pivot 被嵌入到另一个程序中时——基准测试框架、父 agent、无人值守管道——传入 `programmatic=True`：

```python
agent = PivotCodeAgent(
    model="claude-sonnet-4-6",
    cwd="/path/to/experiment",
    permission_mode="yolo",
    programmatic=True,
    extra_tools=[MyDomainTool()],   # optional
)
```

这会断开 Pivot 与项目级和主机级状态的关联，这些状态通常对交互式助手有用，但会污染受控运行：`~/.pivot/PIVOT.md`、`<cwd>/PIVOT.md`、`~/.pivot/memory/MEMORY.md`、AGT 引导，以及网络/git/询问用户类工具（`WebFetch`、`GitCommit`、`AskUserQuestion`、`Skill`）。

使用 `tools=`（完全替换）或 `disabled_tools=`（减法式）来精调工具集。完整行为列表参见 [reference/python-api.md](../reference/python-api.md#programmatic-mode)。

## 在同一 `cwd` 中运行多个 agent

`SessionState` 会对 `<cwd>/.pivot/sessions/<session_id>/session.lock` 加独占锁。两个进程加载同一个 `session_id` 会互相覆盖写入；现在第二个进程会抛出 `pivotcode.session.SessionLockedError`。两个进程在同一 `cwd` 中使用**不同**的会话 ID 则完全正常——会话在 `.pivot/sessions/<id>/` 下按命名空间隔离。

## 相关

- [reference/python-api.md](../reference/python-api.md) —— 完整的类与方法签名。
- [examples/](../../examples/) —— 三个完整示例。
- [architecture/query-loop.md](../architecture/query-loop.md) —— 循环实际如何驱动事件。