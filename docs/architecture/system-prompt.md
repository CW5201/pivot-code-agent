# 系统提示词组装

Pivot 在每次 API 调用中发送的系统提示词由 `pivotcode/prompt/system_prompt.py::get_system_prompt` 组装。它被构建为一个区块列表；供应商决定如何序列化它们（Anthropic：独立的缓存块；OpenAI 兼容：用 `\n\n` 拼接）。

## 组装顺序

| # | 区块 | 来源函数 | 条件？ |
|---|---|---|---|
| 1 | 引言 | `get_intro_section` | 总是 |
| 2 | 系统规则 | `get_system_section` | 总是 |
| 3 | 执行任务 | `get_doing_tasks_section` | 总是 |
| 4 | 谨慎执行操作 | `get_actions_section` | 总是 |
| 5 | 使用你的工具 | `get_using_tools_section(tools)` | 总是 |
| 6 | 语气与风格 | `get_tone_section` | 总是 |
| 7 | 与用户沟通 | `get_communication_section` | 总是 |
| 8 | 会话专属指引 | `get_session_guidance_section` | 总是 |
| 9 | 环境 | `get_environment_section(model, cwd)` | 总是（内容因环境而异） |
| 10 | 可用技能 | `get_skills_section(skills)` | 若注册了 ≥1 个技能 |
| 11 | 记忆 | `build_memory_section(memory_mode, …)` | 总是（关闭时输出简短占位） |
| 12 | 草稿区 | `get_scratchpad_section(scratchpad_dir)` | 正常运行中总是 |
| 13 | `PIVOT.md` 追加 | — | 若 `PIVOT.md` 存在（项目级或全局） |
| 14 | 工具格式指令 | `get_tool_format_system_prompt(fmt, schemas)` | 若设置了 `tool_call_format` |

区块 1–8 是**静态**的——每次调用字节完全相同。区块 9–14 随会话/模式而变化。这种拆分是为 Anthropic 的提示词缓存设计的：区块 1 单独一个、区块 2–8 合在一起、区块 9+ 作为动态块。参见 [prompt-caching.md](prompt-caching.md)。

## `custom_system_prompt` 覆盖

如果设置了 `custom_system_prompt` 设置项，**区块 1–9 会被**该字符串**替换**。区块 10–14（技能、记忆、草稿区、PIVOT.md、工具格式）仍然追加。请谨慎使用——你会丢失内置的所有工具指引、操作安全规则和会话感知能力。

对于增量修改，优先使用 `append_system_prompt`。

## 每个区块的内容

### 1. 引言（总是）

> You are Pivot Code, an open-source coding agent. You are an interactive agent that helps users with software engineering tasks. […]
> IMPORTANT: You must NEVER generate or guess URLs for the user unless you are confident that the URLs are for helping the user with programming.

### 2. 系统规则（总是）

项目符号列表。涵盖：「你输出的所有文本都会显示给用户」「工具在权限模式下运行」「工具结果可能包含 `<system-reminder>` 标签」「对话接近上下文限制时会自动压缩」。

### 3. 执行任务（总是）

最大的区块（约 50 条项目符号）。涵盖：
- 在软件工程任务的上下文中解读请求。
- 在提出修改前何时先读取文件。
- 不要创建用户没有要求的文件。
- 不要给出时间估计。
- 何时先诊断再更换策略。
- 不要添加超出要求范围的功能。
- 不要为不可能发生的情况添加错误处理/校验。
- 默认不加注释（仅在 WHY 不明显时才加）。
- 除非删除代码，否则不要删除已有注释。
- 不做向后兼容的 hack。
- 声称完成之前先验证。
- 未经确认不要运行破坏性命令。
- 如实报告结果。
- 指令看起来不对时询问澄清。

### 4. 谨慎执行操作（总是）

关于可逆性和爆炸半径。列出高风险操作类别（破坏性操作、难以逆转的操作、共享状态操作、第三方上传）并解释默认立场：高风险操作需确认，本地可逆操作直接执行。

### 5. 使用你的工具（总是）

- 当有专用工具（Read/Edit/Write/Glob/Grep）时不要使用 Bash。
- 相互独立的操作可以并行调用多个工具。
- 末尾一行：`Available tools: Bash, Read, Edit, Write, Glob, Grep, AskUserQuestion, WebFetch, GitCommit, Skill`。

### 6. 语气与风格（总是）

- 引用代码时使用 `file_path:line_number` 格式。
- 工具调用前不要在文本末尾使用冒号。

### 7. 与用户沟通（总是）

较长的区块。为真人而写，不是记日志。在关键节点更新进展。注意用户的节奏。让文风与任务匹配。简洁直接。

### 8. 会话专属指引（总是）

- 如果你不理解用户拒绝某次工具调用的原因，去问用户。
- 定向搜索时使用 Glob/Grep。
- 把大规模探索拆成多个步骤。

### 9. 环境（总是，内容因环境而异）

```
# Environment
You have been invoked in the following environment:
 - Primary working directory: <cwd>
 - Is a git repository: Yes
 - Platform: linux
 - Shell: bash
 - OS Version: Linux 6.17.0-14-generic
 - Session started: 2026-04-15 18:42
 - Model: openrouter/google/gemini-2.5-flash

gitStatus:
 <output of `git status` + `git log --oneline -5`>
```

`gitStatus` 块在非 git 目录中被省略。

### 10. 可用技能（条件）

仅在注册了技能时出现。格式：

```
# Available skills

Skills are reusable prompt templates. Users invoke them via `/skill <name> [args]`. You can invoke them via the Skill tool.

- **review-pr** <pr-number or branch>: Review a pull request for correctness...
  TRIGGER: When the user asks for a code review.
```

### 11. 记忆（总是，三种变体）

**`memory=off`**（简短占位）：

> Memory is currently disabled for this session. Do not attempt to read or write memory files. If the user asks to save something, tell them they can enable memory with `/memory on` or `/memory intensive`.

**`memory=on`** 或 **`memory=intensive`**（完整块）：

- 关于全局作用域与项目作用域的引言。
- `## Types of memory` — user / feedback / project / reference / workflow 的 XML 目录。
- `## What NOT to save in memory`。
- `## When to save memories` — 随模式而异（on：仅在用户要求时；intensive：主动保存）。
- `## How to save, update, and remove a memory` — 三步流程，强调**就地更新**而非追加。
- `## When to access memories`。
- `## Before recommending from memory` — 行动前先验证。
- 然后追加 `~/.pivot/memory/MEMORY.md`（全局）和 `<cwd>/.pivot/memory/MEMORY.md`（项目）的完整内容。

### 12. 草稿区（条件）

> You have a session-scoped scratchpad directory at `<cwd>/.pivot/sessions/<id>/scratchpad`. Use it for temporary notes, draft plans, or intermediate work. This directory is session-specific and does not carry over.

### 13. PIVOT.md 追加（条件）

`~/.pivot/PIVOT.md` + `<cwd>/PIVOT.md` 的内容，用 `\n\n` 拼接。仅当至少存在一个文件时才发送。

### 14. 工具格式指令（条件）

用于 `--tool-call-format hermes|glm|pivot`。追加在最后。参见 `pivotcode/tools/text_tool_parser.py`：

- **hermes**：`<tool_call>{"name": ..., "arguments": ...}</tool_call>`
- **glm**：`<tool_call>ToolName<arg_key>k</arg_key><arg_value>v</arg_value></tool_call>`（审计修复后闭合标签现在是必需的）
- **pivot**：`<tool_use>{"name": ..., "input": ...}</tool_use>`

## 供应商特定的组装

### Anthropic

区块作为**缓存块列表**传入，实现细粒度的缓存：

```python
system = [
    {"type": "text", "text": intro, "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "\n\n".join(sections_2_to_8), "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "\n\n".join(dynamic_sections_9_plus)},
]
```

区块 1 有自己独立的缓存断点，因为 `model_info.supports_extended_thinking` 可能在调用之间发生变化，而引言是最稳定的块。

### LiteLLM

LiteLLM 的 `completion(...)` 接受单个 `system` 参数（或一条 `system` 角色的消息）。Pivot 把所有区块用 `\n\n` 拼接成一个字符串：

```python
messages = [{"role": "system", "content": "\n\n".join(sections)}, ...user/assistant messages]
```

没有逐块的缓存（大多数 LiteLLM 后端不支持）。有些后端（经 LiteLLM 的 Anthropic、某些 Gemini 版本）支持——由 LiteLLM 处理转换。

## 检查实际发送的内容

在 GUI 中，**LLM 视角**面板显示当前轮的确切系统提示词。在 Python 中：

```python
agent = PivotCodeAgent(...)
agent._llm_perspective_callback = lambda msgs, sys: print(sys)
# On the next turn, sys is the list of section strings.
```

## 相关

- [concepts/memory.md](../concepts/memory.md) — 区块 11 的详情。
- [concepts/skills.md](../concepts/skills.md) — 区块 10 的详情。
- [concepts/project-context.md](../concepts/project-context.md) — 区块 13 的详情。
- [architecture/prompt-caching.md](prompt-caching.md) — Anthropic 缓存块如何排布。
- [reference/settings.md](../reference/settings.md) — `custom_system_prompt`、`append_system_prompt`、`tool_call_format`。