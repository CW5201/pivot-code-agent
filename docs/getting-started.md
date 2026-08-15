# 入门

本演练带你从 `pip install` 走到第一次成功的代理轮次——大约 10 分钟，无需任何前置设置。

## 1. 安装

```bash
pip install pivotcode
```

需要 Python 3.11+。一次安装即可获得命令行、浏览器图形界面、Python 库、Anthropic 供应商，以及面向其他所有模型供应商的 LiteLLM 支持。

## 2. 提供 API 密钥

任选其一——与你想要使用的供应商相匹配的那个。

```bash
# Anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# OpenRouter —— 一个密钥即可访问 OpenAI、Google、Mistral、Meta 等
export OPENROUTER_API_KEY=sk-or-...

# 直接使用 OpenAI
export OPENAI_API_KEY=sk-...
```

对于本地模型（vLLM、Ollama、SGLang、llama.cpp），无需密钥——参见 [reference/local-models.md](reference/local-models.md)。

## 3. 启动会话

在你想要处理的项目目录内运行：

```bash
pivotcode
```

你会看到：

```
╭──────────────────────────────────────────────────╮
│ Pivot Code -- Open-source coding agent            │
│ Session: a1b2c3d4... | Model: claude-sonnet-4-6  │
│ Type /help for commands, Ctrl+C to interrupt     │
│ Tip: create PIVOT.md (or use /init) to give Pivot project context │
╰──────────────────────────────────────────────────╯

>
```

## 4. 提问

试着问一个只读问题——不需要任何审批：

```
> What does this project do?
```

Pivot 会使用 `Read`、`Glob` 和 `Grep` 工具检查代码库，然后回答。你会看到实时流式文本，接着工具调用以绿色边框面板渲染，最后出现一行总结，类似：

```
Session: 8,118 in + 153 out = $0.0082 (estimated) | Conversation: 8,271 / 200,000 (4%)
```

- `Session` = 该轮次中累计的令牌数与美元成本。
- `Conversation` = 上下文窗口的占用程度。

## 5. 请求修改

```
> Add a docstring to the public functions in pivotcode/agent.py
```

现在 Pivot 会想使用 `Edit`——一个写入工具。默认情况下 Pivot 以 `edit` 权限模式运行，会在写入/执行操作前请求审批：

```
? Allow Edit?
Tool 'Edit' wants to execute with input: {'file_path': '/proj/pivotcode/agent.py', ...}
  1) Allow
  2) Deny
Your choice: 1
```

编辑执行后，你会看到带行号的绿色/红色 diff，精确显示改了什么。审阅后继续。

如果你想在将来允许相同的模式（例如为 Bash 设置 "Allow always `git *` commands"），请使用**选项 3**。该规则会持久化到此项目的 `.pivot/allow_rules.json` 中。

## 6. 使用斜杠命令

斜杠命令输入在提示符中，由本地处理（不经过模型）。试试：

```
> /status
```

你会看到一个表格，包含模型、已用令牌、成本等。一些有用的命令：

- `/help` — 列出所有命令
- `/diff` — 显示未提交更改的 git diff
- `/commit` — 让 Pivot 起草并创建提交
- `/compact` — 手动压缩对话
- `/exit` — 退出会话

完整列表见 [reference/slash-commands.md](reference/slash-commands.md)。

## 7. 恢复会话

```bash
pivotcode --resume
```

恢复此目录中最近一次的会话。对话（最近 100 条消息）会自动重放。

要列出最近的会话并选择其一：

```bash
pivotcode --continue
```

## 8. 尝试不同的模型

供应商是模型字符串的一部分。传输后端会自动推断——你几乎不需要 `--backend`。

```bash
# 通过 OpenRouter 使用 Google Gemini
pivotcode --model openrouter/google/gemini-2.5-pro

# 直接使用 OpenAI
pivotcode --model gpt-4o

# 本地 Ollama
pivotcode --model ollama/qwen2.5-coder:7b --base-url http://localhost:11434

# DashScope / 阿里云千问
pivotcode --model qwen-plus --base-url https://dashscope.aliyuncs.com/compatible-mode/v1
```

完整的供应商/模型矩阵参见 [reference/providers.md](reference/providers.md)。

## 9. 启动图形界面（可选）

```bash
pivotcode --gui
```

打开 `http://localhost:8420/`。三个面板：
- **Chat** — 与命令行相同，但支持就地 diff 渲染。
- **LLM Perspective** — 每轮发送给模型的精确载荷（用于调试）。
- **Git Tree** — 可视化提交 + 代理位置 + 回退/移动控件。

## 10. 给 Pivot 提供项目特定上下文

在项目根目录创建 `PIVOT.md`：

```bash
pivotcode
> /init
```

这会创建一份入门模板。填入你项目的约定：

```markdown
# Pivot's instructions for this project

- Use `pathlib` instead of `os.path`.
- Tests live under `tests/`. Run with `pytest -x`.
- The CLI entry point is `pivotcode.cli.main:main`.
- Don't auto-format — we handle formatting manually with ruff.
```

Pivot 在此项目中启动的每个会话都会把 `PIVOT.md` 加载进系统提示词。你也可以创建 `~/.pivot/PIVOT.md` 文件，用于跨所有项目的全局说明。

## 接下来去哪

- 理解核心概念 → [concepts/agent-loop.md](concepts/agent-loop.md)
- 把 Pivot 当作 Python 库使用 → [guides/building-agents.md](guides/building-agents.md)
- 配置本地模型 → [reference/local-models.md](reference/local-models.md)
- 浏览完整的 CLI 与命令参考 → [reference/cli.md](reference/cli.md)、[reference/slash-commands.md](reference/slash-commands.md)