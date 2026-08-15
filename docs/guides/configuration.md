# 配置

Pivot Code 有许多可调项——供应商、模型、权限模式、压缩阈值、记忆行为等等。本指南解释**设置存放在哪里**以及**它们如何解析**，让你在任何时刻都能预知当前生效的设置。

## 优先级链

每个设置都按以下链条解析，优先级从高到低：

1. **构造函数参数 / CLI 标志** —— 代码中的 `PivotCodeAgent(model="...")`，或命令行上的 `pivotcode --model ...`。始终优先。
2. **会话设置** —— `.pivot/sessions/<id>/settings.json`。会话开始时生效设置的快照，在 `--resume` 时使用，以便恢复的会话保持相同的配置。
3. **项目设置** —— `<cwd>/.pivot/settings.json`。首次运行时自动生成，可选地提交到 git。
4. **内置默认值** —— 硬编码在 `pivotcode/settings.py::SETTINGS_DEFAULTS` 中。

在第 1 层设置的设置会覆盖其下所有层。第 1 层缺失的设置会依次落到第 2 层、第 3 层、第 4 层。

## 三个文件

### `.pivot/settings.json`（项目级）

```json
{
  "backend": "anthropic-native",
  "model": "claude-sonnet-4-6",
  "permission_mode": "edit",
  "memory": "off",
  "compaction_threshold_percent": 75
}
```

首次运行时以合理的默认值自动创建。如果你希望队友采用相同的配置就提交它，不想就加入 gitignore。

使用 `provider` 键（`"litellm"` / `"anthropic"` / `"scripted"`）的旧文件会在首次读取时自动迁移到 `backend`。

### `.pivot/sessions/<id>/settings.json`（每会话快照）

会话开始时自动创建。锁定生效的配置，这样即使你之后修改了 `.pivot/settings.json`，恢复会话时也会使用相同的设置。

你无需手动编辑这些文件——它们由会话系统管理。

### CLI 标志与构造函数参数

```bash
pivotcode --model gpt-4o --permission-mode yolo
```

或者在 Python 中：

```python
PivotCodeAgent(
    model="gpt-4o",
    permission_mode="yolo",
    max_iterations_per_turn=15,
)
```

只传入你想要覆盖的参数——省略的参数会沿链条回退。传输后端由 `model` 推断；只有需要覆盖推断结果时才显式传入 `backend=`。

## 在会话中途修改设置

三种方式：

**斜杠命令**（交互式使用推荐）：
```
> /settings permission_mode=yolo
```

更新会话的生效设置，并持久化到会话快照。立即生效。与后端相关的更改（`backend`、`model`、`api_key`、`base_url`）会重建底层的 `LLMProvider`。更改 `model` 还会重新推断后端（裸 `claude-*` → `anthropic-native`，其他 → `auto`）。

**编辑项目文件**：
```
> /settings-project permission_mode=yolo
```

写入 `.pivot/settings.json`。**不影响**当前会话——只有未来的会话才会采用。当你想要更改此项目的默认设置时使用。

**直接编辑文件**：在编辑器中打开 `.pivot/settings.json`。效果与 `/settings-project` 相同。

## 每个设置键

完整参考：[reference/settings.md](../reference/settings.md)。

要点：

| 键 | 默认值 | 作用 |
|---|---|---|
| `provider` | `litellm` | `litellm`、`anthropic` 或 `scripted` |
| `model` | `anthropic/claude-sonnet-4-6` | 模型标识符（LiteLLM 格式） |
| `permission_mode` | `edit` | `yolo`、`edit`、`safe` |
| `memory` | `off` | `off`、`on`、`intensive` |
| `max_iterations_per_turn` | `None` | 每条用户消息的 API 调用上限 |
| `compaction_threshold_percent` | `80` | 自动压缩触发的时机 |
| `tool_result_max_chars` | `20_000` | Layer A 截断前的单条工具结果大小 |
| `hooks` | `{}` | 工具使用前/后的钩子 |

## API 密钥放在哪里

**不在 `settings.json` 中。** `api_key` 字段被标记为临时字段（`pivotcode/settings.py` 中的 `_EPHEMERAL_FIELDS`）——它从不持久化到磁盘。它只从以下位置读取：

1. CLI：`--api-key sk-...`（一次性，不保存）。
2. 环境：`ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`OPENROUTER_API_KEY` 等。

把密钥放在你的 shell 配置文件中，或由 `direnv` 管理的 `.envrc` 里——标准的开发环境卫生习惯。

## 首次运行设置

在新项目（尚无 `.pivot/`）中首次调用 `pivotcode` 时，一个简短的交互式设置会从你的环境中检测可用的 API 密钥，并写入初始的 `.pivot/settings.json`。

如果你已经使用 Pivot 一段时间，首次运行早已完成——文件已存在，后续运行会跳过设置步骤。

## 向前迁移设置

未来 Pivot 版本新增的设置会在下次加载时自动合并进你现有的 `.pivot/settings.json`：缺失的键获得新默认值，已有的键保留你的自定义。你永远不必重新运行 `/init` 或删除文件来获取新选项。

## 查看当前设置

```
> /settings
```

不带参数时，以 JSON 形式打印完整的生效设置字典。

```
> /settings-project
```

专门打印 `.pivot/settings.json` 文件。

## 区别：会话设置 vs 项目设置

两个文件有 95% 的重叠。区别在于它们各自的角色：

- **项目设置**是本项目声明的基线。
- **会话设置**是当前这个会话正在使用的快照（即使你编辑了项目文件也如此）。

示例：你以 `permission_mode=edit` 启动会话，然后把 `.pivot/settings.json` 改为 `yolo`。这个会话保持 `edit`，直到 `/clear` 或重启。新会话（或对此会话 `--resume`）会采用 `yolo`。

大多数时候你不会注意到这一点——但这解释了为什么在会话中途编辑项目文件似乎不生效。

## 相关

- [reference/settings.md](../reference/settings.md) —— 每个键及其默认值。
- [reference/cli.md](../reference/cli.md) —— 每个 CLI 标志。
- [reference/slash-commands.md](../reference/slash-commands.md) —— `/settings`、`/settings-project`。
- `pivotcode/settings.py` —— 校验器、默认值、加载/保存。