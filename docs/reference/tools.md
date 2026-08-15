# 工具参考

这里介绍每个内置工具及其输入 schema、权限级别和使用说明。关于工具如何与权限和钩子交互的概念性概述，请参阅 [concepts/tools-and-permissions.md](../concepts/tools-and-permissions.md)。

## 汇总表

| 工具 | 权限 | 用途 |
|---|---|---|
| [`Bash`](#bash) | `exec` | 运行 shell 命令 |
| [`Read`](#read) | `read` | 读取文件 |
| [`Edit`](#edit) | `write` | 在文件中进行精确字符串替换 |
| [`Write`](#write) | `write` | 创建或覆盖文件 |
| [`Glob`](#glob) | `read` | 按模式查找文件 |
| [`Grep`](#grep) | `read` | 按正则表达式搜索文件内容 |
| [`AskUserQuestion`](#askuserquestion) | `read` | 向用户提出多选问题 |
| [`WebFetch`](#webfetch) | `read` | 获取 URL |
| [`GitCommit`](#gitcommit) | `write` | 暂存并提交（带提交信息） |
| [`Skill`](#skill) | `read` | 调用用户定义的技能模板 |

所有 schema 都会拒绝未知字段（`additionalProperties: false`）——API 会返回明确的"未知参数"错误，而不是静默丢弃。

---

## Bash

**来源**: `pivotcode/tools/builtin/bash.py`
**权限级别**: `exec`

运行一条 shell 命令。输出为 stdout + stderr 合并结果，退出码非零时将结果标记为 `is_error: true`，以便模型区分成功与失败。

**参数**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `command` | string | 是 | shell 命令。使用 `&&` 串联命令。包含空格的路径请用引号包裹。 |
| `timeout` | integer | 否 | 毫秒；默认为 120 000（2 分钟）。 |
| `purpose` | string | 否 | 显示在审批提示中给用户的一行摘要。 |

**系统提示词指引**: "当存在专门的工具（Read/Edit/Write/Glob/Grep）时，避免使用此工具运行 `cat`、`head`、`tail`、`sed`、`awk` 或 `echo`。"

---

## Read

**来源**: `pivotcode/tools/builtin/file_read.py`
**权限级别**: `read`

带行号读取文件。输出使用 `cat -n` 格式：`<N>\t<line>`。大文件可通过 `offset` + `limit` 切片读取。

**参数**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | string | 是 | 绝对路径（推荐）或相对当前工作目录的路径。 |
| `offset` | integer | 否 | 起始行号（从 1 开始）。 |
| `limit` | integer | 否 | 最大读取行数。默认为 2000。 |

---

## Edit

**来源**: `pivotcode/tools/builtin/file_edit.py`
**权限级别**: `write`

精确字符串替换。当 `old_string` 不唯一时会失败（以防意外的大规模修改），除非设置 `replace_all=true`。

**参数**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | string | 是 | 文件路径。 |
| `old_string` | string | 是 | 精确匹配的字符串，包括空白字符。 |
| `new_string` | string | 是 | 替换后的内容。 |
| `replace_all` | boolean | 否 | 替换所有匹配项。默认为 `false`。 |

**输出**: 包含带 `[ALAN-DIFF]` 标记的统一 diff，在 CLI 中以绿/红颜色显示，在 GUI 中以 diff 块显示。

使用该工具前，你必须先在本会话中 `Read` 过该文件——否则会报错。这可以防止"盲改"式幻觉。

---

## Write

**来源**: `pivotcode/tools/builtin/file_write.py`
**权限级别**: `write`

创建新文件或覆盖已有文件。

**参数**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | string | 是 | 要写入的路径。 |
| `content` | string | 是 | 完整的文件内容。 |

**输出**: 包含 `[ALAN-DIFF]` 统一 diff，显示修改内容（创建文件时则显示完整的新文件内容）。

**Schema 中的指引**: "修改已有文件时优先使用 Edit 工具——它只发送 diff。仅使用此工具创建新文件或进行完全重写。"

---

## Glob

**来源**: `pivotcode/tools/builtin/glob_tool.py`
**权限级别**: `read`

按模式查找文件。使用 `pathlib.Path.glob` 语义（支持 `**`）。

**参数**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `pattern` | string | 是 | 例如 `**/*.py`、`src/*.ts` 等。 |
| `path` | string | 否 | 搜索根目录。默认为当前工作目录。 |

**结果**: 按从新到旧排序的匹配路径列表。最多返回 1000 个匹配项；如果还有更多，输出会明确提示"2000+ 个匹配项中的前 1000 个，请收窄你的模式"。

---

## Grep

**来源**: `pivotcode/tools/builtin/grep_tool.py`
**权限级别**: `read`

对文件内容进行正则搜索。优先使用 `rg`（ripgrep），回退到 GNU `grep`，最后使用纯 Python。每个回退方案都有 30 秒的墙钟时间上限。

**参数**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `pattern` | string | 是 | 正则表达式。 |
| `path` | string | 否 | 目录或文件。默认为当前工作目录。 |
| `glob` | string | 否 | 文件名过滤器，例如 `*.py`。 |
| `output_mode` | string | 否 | `files_with_matches`（默认）、`content`、`count`。 |
| `context` | integer | 否 | 每个匹配项前后各显示的行数（content 模式）。 |

---

## AskUserQuestion

**来源**: `pivotcode/tools/builtin/ask_user.py`
**权限级别**: `read`（无文件系统副作用）

允许模型向用户提出多选问题。

**参数**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `question` | string | 是 | 问题内容。 |
| `options` | list[string] | 是 | 至少 1 个选项。用户可以选择其中一个或自行输入。 |

**Schema 中的使用说明**: "谨慎使用——仅在你确实需要用户输入才能继续时使用。清晰地组织问题并提供可操作的选项。当选择风险较低时，优先做出合理假设，而不是提问。"

在提示处按 Ctrl+C 会中止整个回合（而不只是该工具）。

---

## WebFetch

**来源**: `pivotcode/tools/builtin/web_fetch.py`
**权限级别**: `read`

获取 URL（HTTP/HTTPS）内容，去除 HTML，返回文本内容。

**参数**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `url` | string | 是 | 带协议的完整 URL。 |
| `max_length` | integer | 否 | 截断为 N 个字符。 |

**Schema 指引**: "对于 GitHub URL，建议改为通过 Bash 使用 `gh` CLI（例如 `gh pr view`、`gh issue view`）。"

---

## GitCommit

**来源**: `pivotcode/tools/builtin/git_commit.py`
**权限级别**: `write`

使用给定的提交信息执行暂存和提交。添加 `Co-Authored-By: Pivot Code` 尾注。提交 SHA 会被记录在会话状态中，以便 GUI 的 Git Tree 面板将代理提交标记为蓝色。

**参数**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `message` | string | 是 | 提交信息。 |
| `files` | list[string] | 否 | 要暂存的特定文件。省略则暂存所有更改（`git add -A`）。 |

---

## Skill

**来源**: `pivotcode/tools/builtin/skill_tool.py`
**权限级别**: `read`（加载提示词模板是只读操作）

调用 `.pivot/skills/` 或 `~/.pivot/skills/` 中用户定义的技能。技能正文（替换了 `$ARGUMENTS` 之后）将成为下一条用户消息；模型将对此作出响应。

**参数**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | frontmatter 中的技能名称。 |
| `arguments` | string | 否 | 替换模板中的 `$ARGUMENTS`。 |

如何编写技能，请参阅 [concepts/skills.md](../concepts/skills.md)。

---

## Schema 详情

所有工具 schema 都是与 OpenAI 兼容的 JSON Schema。可通过以下方式获取某个工具的完整 schema：

```python
from pivotcode.tools.registry import tools_to_schemas
from pivotcode.tools.builtin import ALL_BUILTIN_TOOLS

schemas = tools_to_schemas(ALL_BUILTIN_TOOLS)
```

## 工具如何暴露给模型

- **原生工具调用模型**（Anthropic、OpenAI、Gemini 及大多数主流模型）：schema 作为 `tools=[...]` API 参数传入。
- **基于文本的工具调用模型**（GLM、Hermes 微调模型）：schema 列表会被渲染进系统提示词。格式取决于 `tool_call_format` 设置（`hermes`、`glm`、`pivot`）。参见 `pivotcode/tools/text_tool_parser.py`。

## 从 Python 控制工具集

`PivotCodeAgent` 构造函数提供了三个控制开关，按以下顺序应用：

1. **基础集。** `tools=[...]`（显式替换）→ 如果 `programmatic=True` 则使用精选的程序化工具集 → 所有已启用的内置工具（附加 `SkillTool`）。
2. **减去** `disabled_tools=[...]` 中的任何名称。
3. **追加** `extra_tools=[...]` 中的任何内容。

精选的程序化工具集不包含 `WebFetch`、`GitCommit`、`AskUserQuestion` 和 `SkillTool`——适合提示前无人值守、且不希望产生网络/git 副作用的场景。参见 [reference/python-api.md#programmatic-mode](python-api.md#programmatic-mode)。

## 相关

- [concepts/tools-and-permissions.md](../concepts/tools-and-permissions.md) — 心智模型。
- [reference/slash-commands.md](../reference/slash-commands.md) — 面向用户的命令（非工具）。
- [guides/hooks.md](../guides/hooks.md) — 在工具执行前后注入策略。