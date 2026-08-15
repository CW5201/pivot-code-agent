# 项目上下文——`PIVOT.md`

`PIVOT.md` 是项目根目录下的 markdown 文件，Pivot 会在每个会话开始时将其加载到系统提示中。你可以在其中编码**项目特定的约定、约束和上下文**，智能体应始终遵守。

## 创建它

```
> /init
```

创建一个起始模板，包含项目概览、约定和重要文件等部分。之后你可以自由编辑。

或者直接手写文件——它就是纯 markdown。

## `PIVOT.md` 中放什么

好的内容：

```markdown
# Pivot's instructions for this project

## Project overview
This is the Python backend for a contract-management system. Async FastAPI +
PostgreSQL. Key domain concepts: Contract, Party, Obligation.

## Conventions
- Use `pathlib` over `os.path`.
- Async everywhere; no `requests`, use `httpx.AsyncClient`.
- Tests live in `tests/` with pytest-asyncio. Run with `pytest -x`.
- Migrations go through Alembic. Never edit committed migrations — add a new one.

## Important files
- `src/models/` — SQLModel definitions.
- `src/api/` — FastAPI routes.
- `src/services/` — business logic.
- `tests/fixtures/` — shared test fixtures. Prefer adding there over duplicating.

## Things to avoid
- Don't add logging calls to hot paths.
- Don't auto-format — we run ruff manually with a specific config.
- Don't commit anything to `main` — always open a PR.
```

坏的内容（这些应放到记忆或其他地方）：
- ❌ 变更日志或「最近有什么变化」——那是 `git log` 的职责。
- ❌ 机密或 API 密钥——`PIVOT.md` 会被提交到 git。
- ❌ 关于用户个人的信息——那是记忆，不是项目上下文。
- ❌ 临时上下文（「我们正在迁移 X 的过程中」）——直接告诉智能体即可；一个月后就不相关了。

## 全局 `PIVOT.md`

`~/.pivot/PIVOT.md` 是用户级对应物：加载到这台机器上的每个 Pivot 会话中，无论项目是什么。适合用户范围的偏好：

```markdown
## My preferences

- Keep replies under 300 words unless I ask for detail.
- Use UK English spelling.
- When editing code, always run the tests immediately after — don't ask.
```

## 两者都会加载

如果两者都存在，两者都会追加到系统提示中（先全局后项目）。它们互补。

## 在系统提示中的位置

`PIVOT.md` 的内容进入组装后系统提示的**第 13 节**（见 [architecture/system-prompt.md](../architecture/system-prompt.md)）。它位于技能和记忆部分之后，作为「附加块」——Pivot 的内置规则先运行，然后是你的项目规则。如果它们冲突，项目规则胜出，因为它们对当前任务更具体。

## `PIVOT.md` vs 记忆 vs 技能

你可能放置「持久指令」的三个位置：

| 机制 | 作用域 | 结构 | 最适合 |
|---|---|---|---|
| **`PIVOT.md`** | 一个项目（或全局用户级） | 自由格式 markdown | 项目约定、架构笔记、禁止的模式 |
| **记忆** | 按项目或全局 | 带 YAML frontmatter 的结构化 markdown 文件 | 进行中的事实、用户反馈、易过时信息 |
| **技能** | 按项目或全局 | 带 `$ARGUMENTS` 的可调用模板 | 你按需触发的可复用工作流程 |

经验法则：
- 「在这个项目中总是做 X」→ `PIVOT.md`。
- 「用户周二告诉我他们偏好 Y」→ 记忆（`feedback` 类型）。
- 「我说要时执行这个 5 步审查工作流程」→ 技能。

## `/init` 模板

运行 `/init` 会创建：

```markdown
# Project Instructions

<!-- This file is read by Pivot Code at the start of every session. -->
<!-- Use it to give Pivot context about your project, preferences, and conventions. -->

## Project overview

<!-- Describe your project here. What does it do? What technologies does it use? -->

## Conventions

<!-- List coding conventions, naming patterns, or style preferences. -->
<!-- Example: "Use Google-style docstrings", "Prefer pathlib over os.path" -->

## Important files

<!-- Point Pivot to key files or directories it should know about. -->
```

在填写各节时删除注释。

## 相关

- [concepts/memory.md](memory.md) —— 另一种持久化机制。
- [concepts/skills.md](skills.md) —— 可调用的工作流程模板。
- [architecture/system-prompt.md](../architecture/system-prompt.md) —— `PIVOT.md` 内容在提示中的确切位置。
- [reference/slash-commands.md](../reference/slash-commands.md) —— `/init`。