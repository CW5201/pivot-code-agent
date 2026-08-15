# 钩子

钩子（Hook）是 **Pivot 在特定生命周期事件时运行的 shell 命令**。它们让你注入自定义策略而无需修改 agent 代码：审计每一次工具调用、阻止某些操作、预处理输入、发送通知——任何 shell 脚本或小程序能做的事都可以。

## 事件类型

| 事件 | 触发时机 | 能否阻止？ |
|---|---|---|
| `pre_tool_use` | 工具运行之前，权限批准之后。 | ✅ 可以——以 `{"action": "deny"}` 退出。 |
| `post_tool_use` | 工具运行之后。 | 不能。仅用于信息记录。 |
| `session_start` | 会话开始时。 | 不能。 |
| `session_end` | 会话结束时。 | 不能。 |

## 配置

在 `.pivot/settings.json` 中：

```json
{
  "hooks": {
    "pre_tool_use": [
      {
        "command": "python3 /path/to/my_policy_check.py",
        "tools": ["Bash", "Edit", "Write"],
        "timeout": 5
      }
    ],
    "post_tool_use": [
      {
        "command": "/usr/local/bin/audit-log",
        "timeout": 2
      }
    ],
    "session_start": [
      {
        "command": "echo 'Pivot session started'",
        "shell": true
      }
    ]
  }
}
```

每个钩子条目支持以下字段：

| 字段 | 必填 | 默认值 | 用途 |
|---|---|---|---|
| `command` | 是 | — | 要运行的命令。默认通过 `shlex.split` 分词（argv 风格，无 shell 解释）。 |
| `tools` | 否 | `null`（全部） | 此钩子适用的工具名称列表。 |
| `timeout` | 否 | `5` | 钩子被终止前的秒数。 |
| `shell` | 否 | `false` | 选择性启用 shell 解释（管道、重定向、通配符）。**被文档标注为危险路径**——仅在必要时使用。 |

## 钩子如何看到事件

Pivot 通过 **stdin** 向钩子命令发送 JSON 载荷：

```json
{
  "hook_type": "pre_tool_use",
  "tool_name": "Bash",
  "tool_input": {"command": "rm -rf /tmp/scratch"},
  "session_id": "a1b2c3d4..."
}
```

字段取决于事件类型。始终包含 `hook_type`、`session_id`；与工具相关的事件还包含 `tool_name` 和 `tool_input`。

## 钩子如何响应

### `pre_tool_use` —— 控制工具是否运行

钩子的 stdout 会被解析为 JSON：

```json
{"action": "allow"}
```
```json
{"action": "deny", "message": "Blocked: /tmp/scratch is protected"}
```
```json
{"action": "ask", "message": "This looks destructive — are you sure?"}
```

- `allow` —— 工具正常运行。
- `deny` —— 工具被阻止；`message` 会回传给模型，使其能够调整。
- `ask` —— 强制弹出用户权限提示，即使在 `yolo` 模式或本可自动放行的规则下也如此。可作为「针对此模式确认」的守卫使用。

如果钩子输出非 JSON 或没有输出任何内容，则操作默认为 `allow`，**除非**钩子以非零状态退出——这种情况下 Pivot 会以退出码消息进行拒绝。

### 超时与错误

如果钩子超时（默认 5 秒）或崩溃，`pre_tool_use` 会回退为 **`ask`**——一个损坏的安全关键钩子绝不能静默放行。对于 `post_tool_use` / 会话事件（仅信息记录），超时回退为 `allow`，因为没有可阻止的内容。

此回退行为是响应一次审计而收紧的：最初所有地方的超时都默认 `allow`，这使得损坏的安全钩子变得不可见。

## 示例

### 阻止项目目录之外的写入

```bash
#!/usr/bin/env python3
import json, os, sys

payload = json.load(sys.stdin)
if payload.get("tool_name") not in ("Write", "Edit"):
    print(json.dumps({"action": "allow"}))
    sys.exit(0)

path = payload["tool_input"].get("file_path", "")
project_root = os.environ.get("PROJECT_ROOT", os.getcwd())
if os.path.realpath(path).startswith(project_root):
    print(json.dumps({"action": "allow"}))
else:
    print(json.dumps({
        "action": "deny",
        "message": f"Writes outside project root ({project_root}) are blocked.",
    }))
```

保存为 `hooks/no-escape.py`，然后配置：
```json
{
  "hooks": {
    "pre_tool_use": [
      {"command": "python3 hooks/no-escape.py", "tools": ["Write", "Edit"]}
    ]
  }
}
```

### 记录每一次工具调用

```bash
#!/bin/bash
# Read stdin JSON, append to audit log, pass through.
read -r payload
echo "$(date -Is) $payload" >> ~/.pivot-audit.log
echo '{"action": "allow"}'
```

```json
{
  "hooks": {
    "pre_tool_use": [
      {"command": "bash hooks/audit.sh", "shell": false}
    ]
  }
}
```

### 会话结束时发送桌面通知

```json
{
  "hooks": {
    "session_end": [
      {
        "command": "notify-send 'Pivot session ended'",
        "shell": false
      }
    ]
  }
}
```

## 安全模型

- **命令以你的用户权限运行**——与 dotfiles 中任何其他 shell 脚本相同的信任边界。
- **`shell: false` 是默认值**——不解释 shell 元字符，采用 argv 风格执行。这使 `create_subprocess_exec` 成为安全路径。
- **`.pivot/settings.json` 受信任**——如果有人攻破了该文件，他们就能配置任意命令。请像对待你的 `.bashrc` 一样对待它：不要提交未经检查的内容，从不可信来源拉取项目时要谨慎。

## 检查钩子输出

钩子的 `stderr` 会记录在 DEBUG 级别。启用 verbose 模式即可查看：

```bash
pivotcode --verbose
```

或者在设置中设置 `"verbose": true`。

当 `pre_tool_use` 钩子拒绝时，`message` 字段会作为工具结果出现在 agent 的对话中——在图形界面的 Chat 面板和 LLM Perspective 中完全可见。

## 相关

- [reference/settings.md](../reference/settings.md) —— `hooks` 设置键。
- [concepts/tools-and-permissions.md](../concepts/tools-and-permissions.md) —— 钩子在权限管道中的位置。
- `pivotcode/hooks/registry.py` —— 实现。