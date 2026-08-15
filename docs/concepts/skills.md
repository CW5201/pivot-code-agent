# 技能

技能是**用户定义的提示模板**，智能体可以将其作为一等工具调用。可以把它们想象成已保存的工作流程——一条 `/skill review-pr` 命令会展开为模型随后执行的详细多步骤提示。

## 技能的具体形态

`.pivot/skills/`（项目级）或 `~/.pivot/skills/`（全局级）中的 markdown 文件，带有 YAML frontmatter：

```markdown
---
name: review-pr
description: Review a pull request for correctness, style, and unintended changes.
when_to_use: When the user asks for a code review or mentions reviewing a PR.
argument_hint: <pr-number or branch>
allowed-tools: [Bash, Read, Grep]
---

Review the pull request: $ARGUMENTS

Steps:
1. Run `gh pr view $ARGUMENTS` to get the PR metadata.
2. Run `gh pr diff $ARGUMENTS` to see the full diff.
3. Read any test files that changed to understand intent.
4. Check for: correctness bugs, style issues, unintended changes to unrelated files, missing test coverage.
5. Summarize findings — what's good, what needs changes, blocking vs nitpick.
```

当用户或智能体调用 `review-pr` 时，正文（替换 `$ARGUMENTS` 后）会成为下一条用户消息。

## Frontmatter 字段

| 字段 | 必填 | 用途 |
|---|---|---|
| `name` | 是 | 短标识符（推荐 `kebab-case`）。用于 `/skill <name>` 和 Skill 工具调用。 |
| `description` | 是 | 模型读取以判断相关性的一行描述。 |
| `when_to_use` | 否 | 触发提示。在系统提示的技能列表中显示为 `TRIGGER:`。当提示匹配时鼓励模型自主调用该技能。 |
| `argument_hint` | 否 | 在 `/skill list` 中显示，展示预期的参数形态，例如 `<file_path>`。 |
| `allowed-tools` | 否 | 工具过滤器——技能激活时，只有这些工具可用。可以是列表或单个字符串。 |

## 两种调用方式

**1. 用户运行斜杠命令：**

```
> /skill review-pr 123
```

加载技能正文，替换 `$ARGUMENTS = "123"`，作为用户消息输入。

**2. 智能体决定使用 Skill 工具：**

模型在其系统提示（第 10 节）中看到：
```
- **review-pr** <pr-number or branch>: Review a pull request for correctness...
  TRIGGER: When the user asks for a code review or mentions reviewing a PR.
```

如果用户说「review PR #123」，模型可以直接调用 `Skill(name="review-pr", arguments="123")`——无需用户使用 `/skill`。

## 工具过滤

可选的 `allowed-tools` 字段将技能的执行范围限定到工具子集：

```yaml
allowed-tools: [Read, Grep, Glob]
```

技能激活期间只有这些工具可用。在查询循环的第 2 阶段应用——如果技能在第 N 次迭代被调用，第 N+1 次迭代的工具列表会过滤为仅允许的集合。轮次结束时重置。

当你想要一个*必须*保持只读的技能时（例如，一个不应意外修改文件的代码审查技能），这很有用。

## 发现

会话开始时，Pivot 遍历两个技能目录：

- `<cwd>/.pivot/skills/*.md`（项目技能）
- `~/.pivot/skills/*.md`（全局技能）

每个都会被解析；失败会连同文件路径和错误一起出现在日志中。有效的技能列在系统提示的「可用技能」部分。

## 列出技能

```
> /skill list
```

显示发现的每个技能及其描述、参数提示和源文件。

## 创建技能

```
> /skill create
```

交互式地引导创建一个新的技能文件（提示你输入名称、描述、正文）。

或者直接手写文件——技能就是纯 markdown。

## 值得加入工具库的示例

**运行测试并修复失败**：
```markdown
---
name: run-tests
description: Run the test suite and fix any failures.
when_to_use: When the user asks to run tests or check if tests pass.
---

Run `pytest -x` and work through any failures. For each failure:
1. Read the test to understand intent.
2. Read the implementation being tested.
3. Propose a fix (don't apply blindly).
4. Apply the fix.
5. Re-run the affected test.

Stop and report if you hit the same failure twice after different fixes.
```

**起草提交信息**：
```markdown
---
name: commit
description: Review staged changes and draft a commit message.
---

1. Run `git diff --staged`.
2. Draft a 1-2 sentence message following this repo's style (`git log --oneline -10`).
3. Show the message.
4. Commit with GitCommit when approved.
```

**解释一个文件**：
```markdown
---
name: explain
description: Explain what a file does at a conceptual level.
argument_hint: <file_path>
allowed-tools: [Read, Grep, Glob]
---

Read $ARGUMENTS and explain:
1. What this file's job is (one paragraph).
2. Key types and functions (with file:line references).
3. How it fits into the broader codebase (use Grep to find callers).
```

## 校验

Frontmatter 损坏会在会话开始时出现在日志中：

- 缺少 `name` 或 `description` → 技能被跳过并带 WARNING。
- 无效的 YAML → 带解析错误位置的 WARNING。
- `allowed-tools` 不是字符串列表 → 带错误形态的 WARNING。

没有静默失败：如果技能没有加载，你会知道。

## 相关

- [reference/slash-commands.md](../reference/slash-commands.md) —— `/skill list`、`/skill <name>`、`/skill create`。
- [reference/tools.md](../reference/tools.md) —— `Skill` 工具。
- `pivotcode/skills/` —— 源代码：`parser.py`（frontmatter）、`registry.py`（发现）、`tool_filter.py`（作用域）。