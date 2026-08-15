# 斜杠命令

斜杠命令在提示符中输入，由 Pivot Code 本地处理——它们**不会**经过模型。它们以 `/` 开头，在 CLI 和图形界面模式下工作方式相同。

在会话中输入 `/help` 会打印当前已注册的列表。

## 对话控制

| 命令 | 描述 |
|---|---|
| `/clear` | 清除对话并开始新一轮。保留会话文件（之后可用 `--resume`）但丢弃内存中的消息与压缩状态。 |
| `/compact [instructions]` | 手动触发对话压缩。可选指令用于引导总结（例如 `/compact focus on the bug we fixed`）。 |
| `/exit` | 干净地退出会话。 |

## 会话信息

| 命令 | 描述 |
|---|---|
| `/help` | 列出所有可用命令。 |
| `/status` | 完整会话摘要：后端、模型、会话 ID、轮次、消息、详细的令牌分解（常规 / 缓存创建 / 缓存读取 / 输出）、估算美元成本、`cwd`、`PIVOT.md` 与 `.pivot/settings.json` 是否存在。 |
| `/name <text>` | 为此会话设置一个人类可读的名称（显示在列表和图形界面中）。 |

## 模型与后端

| 命令 | 描述 |
|---|---|
| `/model` | 显示当前模型。 |
| `/model <name>` | 在会话中途切换活动模型。会注入一条提醒，让代理知道发生了切换。更改模型还会重新推断后端（裸 `claude-*` → `anthropic-native`；其他任何名称 → `auto`）。 |
| `/backend` | 显示当前的传输后端。 |
| `/backend <name>` | 切换后端（`auto`、`anthropic-native`、`scripted`）。很少需要——后端会从模型字符串推断。 |
| `/provider` | **已弃用**的 `/backend` 别名。接受旧值 `litellm`、`anthropic`、`scripted` 并进行转换。会打印一行弃用通知。 |

## 设置

| 命令 | 描述 |
|---|---|
| `/settings` | 显示当前会话设置。 |
| `/settings <key> <value>` | 更新会话设置（例如 `/settings permission_mode yolo`）。立即生效；后端相关的更改（`backend`、`model`、`api_key`、`base_url`）会重新创建底层的 `LLMProvider`。 |
| `/settings-project` | 显示来自 `.pivot/settings.json` 的项目设置。 |
| `/settings-project <key> <value>` | 更新项目级默认值。 |

## 记忆

| 命令 | 描述 |
|---|---|
| `/memory` | 显示当前记忆模式。 |
| `/memory <mode>` | 设置模式：`off`（默认）、`on`（启动时读取，`/save` 时写入）、`intensive`（在重要响应后也会自动写入）。 |
| `/save [note]` | 让代理将对话中有价值的信息持久化到 `.pivot/memory/` 中。可选笔记会成为保存内容的重点。 |
| `/memodiff` | 显示与上次提交相比的记忆差异。 |

## Git 集成

| 命令 | 描述 |
|---|---|
| `/diff` | 显示所有未提交更改（已暂存 + 未暂存）的 git diff，带语法高亮。 |
| `/commit [message]` | 暂存所有更改并创建提交。不带参数时，使用 AI 生成的消息。 |

## 代理式 Git 树（AGT）

图形界面中的 Git Tree 面板对应这些命令；它们在 CLI 中同样有效。

| 命令 | 描述 |
|---|---|
| `/move <sha-or-branch>` | 将代理移动到某个提交或分支。执行 git checkout 并注入提醒，让代理重新读取文件。 |
| `/revert [N]` | 回退 `N` 个提交（默认 1）。丢弃未提交的更改；对话保留。 |
| `/convrevert [N]` | 仅回退对话中的 `N` 步（代理"忘记"最近的消息）。仓库保持不变。 |
| `/allrevert [N]` | 同时将仓库和对话回退 `N` 步。 |

## 技能

| 命令 | 描述 |
|---|---|
| `/skill list` | 列出可用技能（内置 + 用户自定义）。 |
| `/skill <name> [args]` | 调用技能。代理使用技能的提示词和（可选的）工具过滤器运行。 |
| `/skill create` | 交互式引导创建新的技能文件。 |

## 项目上下文

| 命令 | 描述 |
|---|---|
| `/init` | 在项目根目录创建入门 `PIVOT.md`。`PIVOT.md` 会在会话启动时自动加载到系统提示词中。 |

---

**命令支持自动补全**：在 CLI 中输入 `/` 前缀时会触发（通过 prompt_toolkit）——开始输入斜杠并按 Tab 键循环选择。