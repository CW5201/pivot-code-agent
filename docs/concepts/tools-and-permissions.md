# 工具与权限

Pivot Code 内置少量工具，模型用它们来做实际工作——读取文件、运行 shell 命令、编辑代码、抓取 URL、向你提问。每个工具是否无需批准即可运行取决于两件事：工具的**权限级别**和会话的**权限模式**。

## 内置工具

| 工具 | 作用 | 权限级别 |
|---|---|---|
| `Bash` | 运行 shell 命令。 | `exec` |
| `Read` | 读取文件。 | `read` |
| `Edit` | 在文件内做精确字符串替换。 | `write` |
| `Write` | 创建或覆盖文件。 | `write` |
| `Glob` | 按模式查找文件。 | `read` |
| `Grep` | 按正则搜索文件内容。 | `read` |
| `AskUserQuestion` | 模型向你提出多项选择问题。 | `read` |
| `WebFetch` | 抓取 URL 并去除 HTML。 | `read` |
| `GitCommit` | 以给定消息暂存 + 提交。 | `write` |
| `Skill` | 调用用户定义的技能模板。 | `read` |

完整的模式定义和示例：[reference/tools.md](../reference/tools.md)。

## 权限级别

每个工具声明其影响半径：

- **`read`** —— 对磁盘无副作用，无网络写入。可以安全运行。
- **`write`** —— 修改工作目录中的文件。
- **`exec`** —— 运行任意外部命令（Bash）。

## 权限模式

会话范围内关于何时在运行工具前询问你的立场：

| 模式 | read | write | exec |
|---|---|---|---|
| **`safe`** | ✅ 自动 | 🟡 询问 | 🟡 询问 |
| **`edit`** *(默认)* | ✅ 自动 | 🟡 询问 | 🟡 询问 |
| **`yolo`** | ✅ 自动 | ✅ 自动 | ✅ 自动 |

通过 `--permission-mode` 按会话设置，或在运行时通过 `/settings permission_mode=yolo` 设置。

> **注意**：`safe` 和 `edit` 之间的区别主要影响**钩子**和**允许规则**——两种模式默认都会询问 write/exec，但 `safe` 对降级为自动允许的规则更严格。实践中，大多数用户交互式工作时选择 `edit`，可信的自主运行（如[自动修复循环示例](../../examples/example_2_auto_fix_loop/)）选择 `yolo`。

## 权限提示

当某个工具需要你的批准时，Pivot 会显示：

```
? Allow Bash?
Tool 'Bash' wants to execute with input: {'command': 'git rev-parse HEAD'}
  1) Allow
  2) Deny
  3) Allow always "git *" commands
  Or type your own answer

Your choice: _
```

- **允许** —— 运行这一次调用。
- **拒绝** —— 工具被阻止；模型得到「权限被拒绝」结果并可以调整（例如问你原因）。
- **始终允许** —— 仅对 Bash 显示。提取命令的第一个词（`git`、`npm`、`pytest`...）并记录一条会话级规则，这样未来的 `git *` 调用无需询问即可运行。持久化在 `.pivot/allow_rules.json` 中，因此也适用于该项目未来的会话。
- **输入你自己的答案** —— 你的文本会作为工具结果反馈给模型。适用于没有菜单选项时的「拒绝，并且原因如下」。

在提示处按 Ctrl+C 会干净地中止这一轮。

## 允许规则

规则按项目存在于 `.pivot/allow_rules.json` 中。示例：

```json
[
  {"tool_name": "Bash", "rule_content": "git *", "source": "session"},
  {"tool_name": "Bash", "rule_content": "pytest *", "source": "session"},
  {"tool_name": "Read", "rule_content": null, "source": "project"}
]
```

- `rule_content: null` = 全覆盖规则，匹配任何输入。
- `rule_content: "pattern *"` = 当目标字段（Bash 的 `command`、Read/Edit/Write 的 `file_path` 等）以 `pattern` 开头时匹配。

匹配是按工具字段进行的，而不是扫描参数中的每个字符串——因此 `Read: "config*"` 规则匹配 `file_path="config.json"`，但不会匹配无关字段。精确逻辑见 [`pivotcode/permissions/pipeline.py`](https://github.com/example/pivot-code/blob/main/pivotcode/permissions/pipeline.py)。

## 拒绝规则

目前没有用于添加拒绝规则的 CLI——这个概念存在于代码中（`pivotcode/permissions/context.py::PermissionRule` 带 `behavior=DENY`），但没有面向用户的命令来填充它。如果你需要对某个工具做全面阻止，请配置[工具使用前钩子](../guides/hooks.md)。

## 钩子——自定义策略的逃生舱

钩子是 shell 命令（或 argv 风格命令；参见 `shell: false` 默认值），Pivot 在每次工具调用前后运行。`PreToolUse` 钩子可以检查工具名称 + 输入，向 stdout 打印 `{"action": "deny", "message": "..."}`，Pivot 就会阻止该调用并将该消息传给模型。

真实用例：
- 阻止触碰生产目录的 Bash 命令。
- 要求 `rg` 查询限定在特定路径内。
- 将每次编辑记录到审计文件。

配置和实例见 [guides/hooks.md](../guides/hooks.md)。

## 工具调用实际如何运行（一次迭代）

1. 模型通过流发出一个 `tool_use` 块。
2. 循环收集本次迭代的所有 `tool_use` 块。
3. 对每个块，并行（只读工具）或串行（写入工具）：
   - `tool.validate_input(args, ctx)` —— 结构检查。
   - 工具使用前钩子触发（如果已配置）。
   - 权限管线：允许规则？拒绝规则？模式自动？否则询问用户。
   - `tool.call(args, ctx)` —— 实际工作。
   - 工具使用后钩子触发。
4. 每个工具的 `ToolResult.data` 成为 `UserMessage` 中 `tool_result` 块的内容。
5. 该消息在下一次迭代中发送回模型。

`run_tool_use` 见 `pivotcode/tools/execution.py`，并发批处理逻辑（默认 `max_tool_concurrency = 10`）见 `pivotcode/tools/orchestration.py`。

## 相关

- [reference/tools.md](../reference/tools.md) —— 完整的工具模式定义。
- [guides/hooks.md](../guides/hooks.md) —— 钩子配置。
- [reference/settings.md](../reference/settings.md) —— `permission_mode`、`max_tool_concurrency`。
- [concepts/git-tree.md](git-tree.md) —— `GitCommit` 与 AGT 的集成。