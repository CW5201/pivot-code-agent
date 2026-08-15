# 开发

为贡献搭建 Pivot Code。

## 前置条件

- Python 3.11 或更新版本。
- Git。
- 可选：将 [ripgrep](https://github.com/BurntSushi/ripgrep)（`rg`）添加到 PATH，以获得更快的开发时 Grep 工具性能。

## 克隆 + 安装

```bash
git clone https://github.com/<your-fork>/pivot-code.git
cd pivot-code
python -m venv venv
source venv/bin/activate
pip install -e '.[dev]'
```

`-e` 以可编辑模式安装——对 `pivotcode/` 的更改会立即生效，无需重新安装。`[dev]` 拉取测试工具（pytest、pytest-asyncio、pytest-cov、ruff）。

## 从源码树运行 Pivot

可编辑安装之后：

```bash
pivotcode --version    # reads from pivotcode/__version__.py
pivotcode              # runs from your working copy
```

编辑 `pivotcode/` 下的文件，保存，重新运行——更改即生效。

## 运行测试

```bash
pytest -x -q
```

- `-x` 在第一个失败处停止。迭代时很有用。
- `-q` 抑制每个测试的输出。去掉可获得详细的逐测试输出。

运行特定文件：

```bash
pytest tests/unit/test_compaction.py
```

运行匹配关键字的测试：

```bash
pytest -k "hook or permission"
```

带覆盖率运行：

```bash
pytest --cov=pivotcode --cov-report=term-missing
```

测试组织见 [contributing/testing.md](testing.md)。

## 代码检查

```bash
ruff check .
```

带自动修复：

```bash
ruff check --fix .
```

没有单独的格式化器——`ruff format` 可用，但我们不强求。与周围风格保持一致；重构无关代码的 PR 很可能会被退回。

## 项目结构

```
pivot-code/
├── pivotcode/              # the package
│   ├── agent.py           # PivotCodeAgent class
│   ├── query/             # query_loop + state
│   ├── providers/         # Anthropic, LiteLLM, Scripted
│   ├── tools/             # built-in tools + orchestration
│   ├── messages/          # message dataclasses + normalization
│   ├── session/           # session persistence, state, transcripts
│   ├── permissions/       # permission pipeline + rules
│   ├── compact/           # 3-layer compaction
│   ├── hooks/             # pre/post tool-use hooks
│   ├── memory/            # memory system
│   ├── skills/            # skill registry + parser
│   ├── git_tree/          # AGT operations
│   ├── cli/               # CLI entry point + REPL + display
│   ├── gui/               # browser GUI (FastAPI + WebSocket + static/)
│   ├── prompt/            # system prompt assembly
│   ├── api/               # retry, cost tracking
│   └── utils/             # atomic I/O, token counting, env helpers
├── tests/
│   ├── unit/              # fast, no-network, no-disk tests
│   ├── integration/       # full agent runs with scripted provider
│   └── conftest.py
├── examples/              # runnable example scripts
├── docs/                  # these files
├── pyproject.toml
├── LICENSE
└── README.md
```

从 `pivotcode/query/loop.py::query_loop` 开始阅读。完整的子系统地图见 [architecture/overview.md](../architecture/overview.md)。

## 使用 scripted provider

对于涉及代理循环或消息处理的代码，使用 `ScriptedProvider` 编写确定性测试，无需调用真实 API：

```python
from pivotcode.providers.scripted_provider import ScriptedProvider, text, tool_call

provider = ScriptedProvider.from_responses([
    tool_call("Bash", {"command": "ls"}),
    text("Found 3 files"),
])

agent = PivotCodeAgent(backend=provider, permission_mode="yolo")
```

示例见 `tests/integration/test_agent_loop.py`。

## 开发期间运行 GUI

```bash
pivotcode --gui
```

静态资源位于 `pivotcode/gui/static/`（HTML、JS、CSS）。浏览器会积极缓存它们——编辑 JS/CSS 后请**强制刷新**（Ctrl+Shift+R / Cmd+Shift+R）。普通刷新不会生效。

编辑 `pivotcode/gui/server.py` 或 `gui_ui.py` 需要重启 `pivotcode`（Python 端不会热重载）。

## 进行更改——典型流程

1. 创建功能分支：`git checkout -b feat/my-feature`。
2. 进行更改。保持提交小而聚焦。
3. 运行测试：`pytest -x -q`。修复回归。
4. 运行代码检查：`ruff check .`。
5. 如果更改了代理行为：在临时目录中使用 `pivotcode --model openrouter/google/gemini-2.5-flash --permission-mode yolo` 运行真实模型冒烟测试。
6. 如果更改了提示词行为：使用 `--gui` 并检查 LLM 视角面板，验证模型看到的符合预期。
7. 推送并打开 PR。

## 提交信息风格

查看 `git log --oneline -20` 了解当前风格。通常：
- 简短的祈使句（"添加 X"、"修复 Y"、"重命名 Z"）。
- 无作用域前缀（这不是 Conventional Commits）。
- 首行不超过 72 个字符；仅在需要时在正文中展开。

## 常见任务

### 添加新工具

1. 创建 `pivotcode/tools/builtin/my_tool.py`，继承 `Tool`，实现 `name`、`description`、`input_schema`、`permission_level`、`call`。
2. 在 `pivotcode/tools/builtin/__init__.py` 中注册（导入并添加到 `ALL_BUILTIN_TOOLS`）。
3. 在 `tests/unit/test_tools.py` 中添加 schema 测试。
4. 在 `docs/reference/tools.md` 中记录。

### 添加新设置

1. 添加到 `pivotcode/settings.py::SETTINGS_DEFAULTS`。
2. 如果类型需要检查，在 `_VALIDATORS` 中添加校验器。
3. 传播到使用它的地方（通常是 `QueryParams` → `query_loop`）。
4. 如果合适，在 `pivotcode/cli/main.py` 中添加 CLI 标志。
5. 在 `docs/reference/settings.md` 中记录。

### 添加新斜杠命令

1. 在 `pivotcode/cli/repl.py` 的 `SLASH_COMMANDS` 字典中添加条目。
2. 在 `_handle_slash_command` 中添加分发分支。
3. 编写 `_handle_<command>` 函数。
4. 在 `tests/unit/test_repl.py`（或集成测试）中添加测试。
5. 在 `docs/reference/slash-commands.md` 中记录。

## 相关

- [contributing/testing.md](testing.md) — 测试组织 + 约定。
- [contributing/release.md](release.md) — PyPI 发布流程。
- [architecture/overview.md](../architecture/overview.md) — 系统全貌（万英尺视角）。