# 测试

Pivot Code 有大约 700 个测试，分为三个层级。

## 运行

```bash
# All tests (unit + integration)
pytest

# Stop at first failure, quieter output
pytest -x -q

# Just one file
pytest tests/unit/test_compaction.py

# Match a name keyword
pytest -k "hook or permission"

# Coverage
pytest --cov=pivotcode --cov-report=term-missing
```

## 组织

```
tests/
├── conftest.py         # shared fixtures (tmp session dirs, scripted providers, etc.)
├── unit/               # fast, local, no-network
└── integration/        # full agent turns against the scripted provider
```

### `unit/`

每个子系统对应一个文件。无网络，除 `tmp_path` 外无磁盘访问。每个测试应在约 100 ms 内运行。

关键文件：
- `test_compaction.py` — 第 A、B、C 层逻辑。
- `test_permissions_extended.py` — 权限流水线、允许规则、模式。
- `test_messages.py` — 消息类型、序列化、规范化。
- `test_session.py`、`test_session_listing.py` — `SessionState`、transcript 往返、`find_session_by_prefix`。
- `test_settings.py` — 默认值、校验器、保存/加载。
- `test_tools.py` — 工具输入校验、schema。
- `test_text_tool_parser.py` — hermes/glm/pivot 格式解析器。
- `test_hooks.py` — 前置/后置钩子执行、超时、动作回退。
- `test_skills.py` — frontmatter 解析器、注册表、校验。
- `test_agt_operations.py` — AGT 移动/回退原语。
- `test_compaction_upgrade.py` — `format_compact_summary`、9 节提示词。
- `test_thinking_extraction.py` — 基于文本的解析器中的 `ThinkingBlock` 提取。

### `integration/`

通过 `PivotCodeAgent.query_events_async` 运行完整代理循环的测试，由 `ScriptedProvider` 支撑以保证确定性。

- `test_agent_loop.py` — 正常路径 + `max_iterations_per_turn` + 提前退出。
- `test_reactive_scenarios.py` — 错误恢复、多工具场景。
- `test_query_api.py` — 2×2 矩阵（同步/异步 × 文本/事件）。
- `test_scripted_ui.py` — scripted UI fixture。
- `test_agt_edge_cases.py` — 针对真实 git 仓库 fixture 的 AGT 操作。
- `test_gui_phase2.py` — 使用 scripted UI 的 GUI 事件流。

## 编写新测试

### 单元测试模板

```python
import pytest
from pivotcode.compact.compact_truncate import compaction_truncate_tool_results
from pivotcode.messages.types import UserMessage, ToolResultBlock


def test_truncates_oversized_result():
    big = "X" * 50_000
    messages = [
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content=big)]),
    ]
    result = compaction_truncate_tool_results(
        messages, max_chars=10_000,
    )
    assert "[ALAN-TRUNCATED]" in str(result[0].content)
```

### 集成测试模板

```python
import pytest
from pivotcode import PivotCodeAgent
from pivotcode.providers.scripted_provider import ScriptedProvider, text


@pytest.mark.asyncio
async def test_simple_turn():
    provider = ScriptedProvider.from_responses([text("Hello!")])
    agent = PivotCodeAgent(backend=provider, permission_mode="yolo")
    answer = await agent.query_async("ping")
    assert answer == "Hello!"
```

`pyproject.toml` 中设置了 `asyncio_mode = "auto"`，因此异步测试只需 `@pytest.mark.asyncio`。

## 值得了解的 fixture

在 `tests/conftest.py` 中：

- `tmp_cwd` — 用于会话状态的临时目录。
- `tmp_git_repo` — 已初始化的 git 仓库（用于 AGT 测试）。
- `scripted_agent` — 预构建的带 `ScriptedProvider` 的 `PivotCodeAgent`。

查看 `conftest.py` 获取当前列表。

## 不需要测试的内容

- 真实 API 调用。使用 `ScriptedProvider`。如果你确实需要针对真实模型验证行为，请在推送前手动进行——不要加入 CI。
- 超出冒烟测试范围的显示格式。Rich 的输出是实现细节；过度指定会导致脆弱的测试。
- 公共行为已覆盖时的私有方法内部实现。优先使用黑盒测试。

## 何时添加测试

始终：
- Bug 修复 → 覆盖原始失败输入的回归测试。
- 新工具 / 新斜杠命令 → 至少一个验证其可运行且能校验输入的冒烟测试。
- 新设置 → 测试其校验器正常工作且循环尊重该设置。
- 新的压缩行为 → 测试其修复的具体场景。

跳过：
- 琐碎的显示 / 仅重构的更改。
- 文档字符串更新。

## CI

目前仓库在本地运行 `pytest -x -q`。CI 集成（GitHub Actions）已计划但尚未到位——贡献者需要在推送前运行测试。

## 调试失败的测试

```bash
pytest tests/path/to/test.py::TestClass::test_name -v
```

`-v` 显示每个断言的行。添加 `-s` 可查看 `print()` 输出（pytest 默认会捕获它）。

对于挂起的异步测试：

```bash
pytest tests/... --timeout=10
```

（需要 `pytest-timeout`；不在我们的开发依赖中，但本地添加很容易。）

## 相关

- [contributing/development.md](development.md) — 搭建与开发工作流。
- [architecture/overview.md](../architecture/overview.md) — 每个子系统的作用（指导在哪里为更改添加测试）。