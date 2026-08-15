# 架构总览

Pivot Code 如何组织在一起的万米高空视角。如果你即将深入代码，请先读这一篇。

## 各层

```
┌─────────────────────────────────────────────────────────────────┐
│  UI layer           CLIUI      GUIUI      ScriptedUI            │
│                        │          │             │               │
│                        └──────┬───┴─────────────┘               │
│                               │ SessionUI interface             │
├───────────────────────────────┼─────────────────────────────────┤
│  Session layer        run_session (pivotcode/cli/repl.py)        │
│                               │                                 │
├───────────────────────────────┼─────────────────────────────────┤
│  Agent layer          PivotCodeAgent (pivotcode/agent.py)         │
│                               │ .query_events_async             │
├───────────────────────────────┼─────────────────────────────────┤
│  Loop layer           query_loop (pivotcode/query/loop.py)       │
│                               │ phases 1–10 per iteration       │
├───────────────────────────────┼─────────────────────────────────┤
│  Support          providers   tools   compact   hooks           │
│  subsystems       messages    permissions   memory   skills     │
│                   session     git_tree                          │
└─────────────────────────────────────────────────────────────────┘
```

每层只有一个职责：

- **UI 层**向用户呈现事件（终端、浏览器、测试框架）。三者都实现 `SessionUI`（`pivotcode/gui/base.py`）。
- **会话层**是 REPL 驱动。处理斜杠命令、向 UI 显示事件、循环运行 `run_session`。
- **代理层**是公共 API。`PivotCodeAgent` 拥有消息列表、会话状态和供应商；对外暴露 `query`/`query_async`/`query_events`/`query_events_async`。
- **循环层**是内部引擎。`query_loop` 是一个运行单个「轮」的异步生成器——反复调用供应商并执行工具。
- **支撑子系统**是其余一切，按关注点分组。

## 一个轮的数据流

```
user types "fix this bug"
        │
        ▼
  CLIUI.get_input returns the string
        │
        ▼
  run_session sees it, not a slash-command, so calls:
        agent.query_events_async("fix this bug")
        │
        ▼
  PivotCodeAgent.query_events_async:
      - appends UserMessage to self._messages
      - builds QueryParams with the provider, tools, settings, abort event
      - calls query_loop(params)
        │
        ▼
  query_loop (while True):
     phase 1: abort check
     phase 1.5: inject date/time system-reminder
     phase 2: compaction pre-check (truncate → clear → auto)
     phase 3: blocking limit check
     phase 4: provider.stream() — streams response
     phase 5: collect content blocks into AssistantMessage
     phase 6: yield AssistantMessage to caller
     phase 7: abort check
     phase 8: execute tools (orchestration.py runs them concurrent/serial)
             for each tool:
                 validate → pre-hook → permission pipeline → tool.call → post-hook
     phase 8.5: memory reminder (intensive mode)
     phase 9: check max_iterations_per_turn
     phase 10: loop back
        │
  (or exit on "no tool use" terminal condition)
        │
        ▼
  Events yielded back up to agent.query_events_async, which:
      - appends them to self._messages (filtered)
      - yields them to the caller (run_session)
        │
        ▼
  run_session receives each event:
      - ui.on_agent_event(event) → displayed
      - after loop: ui.on_cost(...) → displays cost summary
```

## 关键包

### `pivotcode.agent`
`PivotCodeAgent` — 公共 API。

### `pivotcode.query`
- `loop.py` — `query_loop` 异步生成器，跳动的心脏。
- `state.py` — `LoopState` 数据类，迭代之间的可变状态。

### `pivotcode.providers`
- `base.py` — `LLMProvider` ABC、`StreamEvent` 类型。
- `anthropic_provider.py` — 直连 Anthropic SDK 的封装。
- `litellm_provider.py` — LiteLLM 适配器。
- `scripted_provider.py` — 确定性的测试供应商。

### `pivotcode.tools`
- `base.py` — `Tool` ABC 和 `ToolUseContext`。
- `registry.py` — 枚举内置工具，转换为 API schema。
- `execution.py` — `run_tool_use` — 校验 + 权限 + 调用 + 钩子。
- `orchestration.py` — 批量工具调用（读操作并发、写操作串行）。
- `builtin/*.py` — 10 个内置工具。
- `text_tool_parser.py` — 面向非原生模型的 hermes/glm/pivot 格式。

### `pivotcode.messages`
- `types.py` — 所有消息数据类（UserMessage、AssistantMessage、块）。
- `factory.py` — 常见消息的构造函数。
- `normalization.py` — 内部消息 → API 就绪形式。
- `serialization.py` — 转换为 OpenAI 兼容字典。

### `pivotcode.session`
- `session.py` — 会话列举、加载/保存设置快照。
- `state.py` — `SessionState` 磁盘关联属性（turn_count、cost、allow_rules 等）。
- `transcript.py` — JSONL 转录本的序列化/反序列化。

### `pivotcode.permissions`
- `context.py` — `PermissionMode`、`PermissionBehavior`、`PermissionRule`、`ToolPermissionContext`。
- `pipeline.py` — `check_permissions` 决定允许/拒绝/询问。
- `project_rules.py` — 项目级 `.pivot/allow_rules.json` 持久化。

### `pivotcode.compact`
- `compact_truncate.py` — 层 A（逐工具结果截断）。
- `compact_clear.py` — 层 B（清除旧工具结果）。
- `compact_auto.py` — 层 C（分叉代理摘要）。
- `prompt.py` — 9 个区块的摘要模板。

### `pivotcode.hooks`
- `registry.py` — 加载/执行 pre/post tool-use 钩子。
- `handlers.py` — 会话开始/会话结束的钩子入口点。

### `pivotcode.memory`
- `memdir.py` — 目录结构、记忆索引加载。
- `prompt.py` — 系统提示词的记忆区块（off/on/intensive 变体）。

### `pivotcode.skills`
- `registry.py` — 从 `.pivot/skills/` 发现技能。
- `parser.py` — YAML frontmatter 解析器。
- `tool_filter.py` — 技能激活时限定工具访问范围。

### `pivotcode.git_tree`
- `parser.py` — 把 git log 解析为 AGT 模型。
- `layout.py` — 为 GUI 树分配 (x, y) 坐标。
- `operations.py` — `agt_move`、`agt_revert` 等。
- `memory_snapshots.py` — 跨移动保存/恢复 `.pivot/memory/`。

### `pivotcode.cli`
- `main.py` — argparse 入口点。
- `repl.py` — `run_session` + 斜杠命令处理器。
- `display.py` — 基于 Rich 的渲染（欢迎面板、diff 等）。
- `user_input.py` — 权限提示的 `ask_user_cli`（prompt-toolkit）。

### `pivotcode.gui`
- `base.py` — `SessionUI` 接口。
- `cli_ui.py` — 使用 Rich + prompt-toolkit 的终端实现。
- `gui_ui.py` — FastAPI + WebSocket 实现。
- `server.py` — FastAPI 应用工厂。
- `static/` — 浏览器 UI 的 HTML / JS / CSS。
- `scripted_ui.py` — 确定性的测试 UI。
- `serialization.py` — 把代理事件转换为线上格式字典。

### `pivotcode.api`
- `retry.py` — 供应商流周围的 `with_retry` 封装。
- `cost_tracker.py` — 每会话成本核算 + Anthropic 定价。

### `pivotcode.utils`
- `tokens.py` — 基于 tokenizer 的压缩预检查计数。
- `atomic_io.py` — `atomic_write_json` / `atomic_write_text`（tmp + rename）。
- `env.py` — `get_cwd`、`get_git_status`、`is_git_repo` 等。

## 公共 API 表面

用户从 `pivotcode` 导入：

```python
from pivotcode import PivotCodeAgent
```

目前没有其他稳定的公共导出。内部模块会在版本之间变化。

## 从哪开始读代码

如果你想理解 Pivot 的工作方式：

1. **[`pivotcode/query/loop.py`](https://github.com/example/pivot-code/blob/main/pivotcode/query/loop.py)** — 整个循环都在一个文件里。从 `query_loop` 开始读各个阶段。
2. **[`pivotcode/agent.py`](https://github.com/example/pivot-code/blob/main/pivotcode/agent.py)** — 看看 `query_events_async` 如何接线 `query_loop`。
3. **[`pivotcode/prompt/system_prompt.py`](https://github.com/example/pivot-code/blob/main/pivotcode/prompt/system_prompt.py)** — 系统提示词区块。
4. **[`pivotcode/messages/normalization.py`](https://github.com/example/pivot-code/blob/main/pivotcode/messages/normalization.py)** — 内部消息如何变成 API 载荷。
5. **[`pivotcode/tools/execution.py`](https://github.com/example/pivot-code/blob/main/pivotcode/tools/execution.py)** — 每个工具的执行路径。

## 相关

- [architecture/query-loop.md](query-loop.md) — 逐阶段讲解。
- [architecture/system-prompt.md](system-prompt.md) — 系统提示词如何组装。
- [architecture/messages-and-api.md](messages-and-api.md) — 消息归一化流水线。
- [architecture/prompt-caching.md](prompt-caching.md) — Anthropic 缓存块策略。