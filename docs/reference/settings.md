# 设置参考

`.pivot/settings.json` 中的每个键及其默认值、类型和作用。设置如何通过优先级链解析，请参阅 [guides/configuration.md](../guides/configuration.md)。

## 快速参考表

| 键 | 类型 | 默认值 | 领域 |
|---|---|---|---|
| `backend` | string | `anthropic-native` | 后端 |
| `model` | string | `claude-sonnet-4-6` | 后端 |
| `api_key` | string \| null | `null`（来自环境变量） | 后端 — 临时性，不持久化 |
| `base_url` | string \| null | `null` | 后端 |
| `tool_call_format` | string \| null | `null` | 后端 — `hermes`、`glm`、`pivot` |
| `permission_mode` | string | `edit` | 会话 |
| `max_iterations_per_turn` | int \| null | `null`（无限制） | 会话 |
| `max_output_tokens` | int \| null | `null` | 会话 |
| `custom_system_prompt` | string \| null | `null` | 系统提示词 |
| `append_system_prompt` | string \| null | `null` | 系统提示词 |
| `memory` | string | `off` | 记忆 |
| `verbose` | bool | `false` | 日志 |
| `hooks` | object | `{}` | 钩子 |
| `compact_max_output_tokens` | int | `20_000` | 压缩 |
| `capped_default_max_tokens` | int | `8_000` | 输出控制 |
| `escalated_max_tokens` | int | `64_000` | 输出控制 |
| `auto_compact_buffer_tokens` | int | `13_000` | 压缩 |
| `warning_threshold_buffer_tokens` | int | `20_000` | 压缩 |
| `blocking_limit_buffer_tokens` | int | `3_000` | 压缩 |
| `max_consecutive_compact_failures` | int | `3` | 压缩 |
| `compaction_threshold_percent` | int | `80` | 压缩 |
| `max_compact_ptl_retries` | int | `3` | 压缩 |
| `max_output_tokens_recovery_limit` | int | `3` | 错误恢复 |
| `max_tool_concurrency` | int | `10` | 工具执行 |
| `tool_result_max_chars` | int | `20_000` | 工具执行 |
| `compact_clear_keep_recent` | int | `10` | 压缩 |
| `thinking_budget_default` | int | `10_000` | 思考 |
| `memory_reminder_threshold` | int | `10` | 记忆 |
| `max_scratchpad_sessions` | int | `5` | 会话 |
| `compaction_truncate_enabled` | bool | `true` | 压缩层开关 |
| `compaction_clear_enabled` | bool | `true` | 压缩层开关 |
| `compaction_auto_enabled` | bool | `true` | 压缩层开关 |

权威来源：`pivotcode/settings.py::SETTINGS_DEFAULTS`。

---

## 后端

### `backend`

传输层（高级选项——未显式设置时从 `model` 推断）。
- `"anthropic-native"` — 直接使用 Anthropic SDK（`cache_control`、原生思考、原生 `tool_use`）。裸 `claude-*` 模型名称的默认值。
- `"auto"` — 通用 LiteLLM 传输层（OpenAI、OpenRouter、Gemini、Vertex、Bedrock、Ollama、vLLM、SGLang、本地服务器）。其他所有情况的默认值。
- `"scripted"` — 确定性测试后端。参见 [reference/python-api.md](python-api.md)。

旧版 `provider` 键（`"litellm"` / `"anthropic"` / `"scripted"`）会在首次读取时自动迁移为 `backend`。

### `model`

模型标识符。裸名称（`claude-sonnet-4-6`、`gpt-4o`）或 LiteLLM 风格的 `provider/model` 前缀（`openrouter/google/gemini-2.5-pro`、`ollama/llama3.1`、`anthropic/claude-sonnet-4-6`）。

在会话中途更改 `model` 也会重新推断 `backend`（裸 `claude-*` → `anthropic-native`，其他 → `auto`）。

### `api_key`

如果为 `null`，则在初始化时从供应商的环境变量中读取。**绝不会持久化到磁盘**（标记为临时性）。

### `base_url`

覆盖 API 端点。用于本地服务器（`http://localhost:8000/v1`）。

### `tool_call_format`

适用于不支持原生函数调用的模型的基于文本的工具调用协议。可选值：`"hermes"`、`"glm"`、`"pivot"`。设置后，工具定义会被注入系统提示词，而不是作为 API 工具 schema 传入，模型输出的文本会被解析以提取工具调用。`null`（默认值）表示使用模型的原生函数调用。

---

## 会话

### `permission_mode`

- `"yolo"` — 允许一切操作，无需询问。
- `"edit"`（默认）— 允许读取，写入/执行需询问。
- `"safe"` — 除纯读取外，一切操作都需询问。

### `max_iterations_per_turn`

每条用户消息对应的 API 调用次数硬上限。`null` = 无限制。防止失控循环；忽略本应自然停止的推理循环。

### `max_output_tokens`

每次调用的输出 Token 上限。在恢复过程中内部会最多升级至 `escalated_max_tokens`。

---

## 系统提示词

### `custom_system_prompt`

完全替换 Pivot 内置的系统提示词（第 1–9 节）。第 10–14 节（技能、记忆、scratchpad、PIVOT.md、工具格式）仍会追加。请谨慎使用——你将失去所有内置的工具使用指引和安全指令。

### `append_system_prompt`

追加到 Pivot 内置的系统提示词之后。比起 `PIVOT.md`，这是注入项目特定提示的更安全方式。无法被 Anthropic 缓存（每个会话都会变化）。

---

## 记忆

### `memory`

- `"off"`（默认）— 不读写记忆文件。
- `"on"` — 启动时读取，仅在显式 `/save` 或用户请求时写入。
- `"intensive"` — 还会在重要回合后主动保存。

### `memory_reminder_threshold`

在 `intensive` 模式下，两次记忆保存提醒之间的迭代次数。默认为 10。

---

## 日志

### `verbose`

如果为 `true`，则向 stderr 输出 debug 级别的日志。与 `--verbose` 标志效果相同。

---

## 钩子

### `hooks`

将钩子类型名称映射到钩子配置列表的字典。schema 和示例请参阅 [guides/hooks.md](../guides/hooks.md)。

---

## 压缩

### `compaction_threshold_percent`

C 层（自动压缩）触发的时机，以上下文窗口的百分比表示。默认为 80。

### `tool_result_max_chars`

A 层会将超过此长度的任何单个工具结果截断。默认为 20 000 字符。

### `compact_clear_keep_recent`

B 层会清除旧的工具结果，但保留最近的 N 条。默认为 10。

### `compact_max_output_tokens`

C 层摘要调用的输出预算。默认为 20 000。

### `auto_compact_buffer_tokens`

紧急压缩触发器——当预测的 Token 数落在距上下文上限此范围内的余量时触发。默认为 13 000。

### `warning_threshold_buffer_tokens`

面向用户的"上下文即将占满"警告触发器。默认为 20 000。

### `blocking_limit_buffer_tokens`

硬性下限——拒绝接近上限至此范围的 API 调用。默认为 3 000。

### `max_consecutive_compact_failures`

熔断器阈值。连续 N 次压缩失败后，Pivot 会显示错误并停止尝试。默认为 3。

### `max_compact_ptl_retries`

压缩摘要步骤本身出现提示词过长时的重试次数。默认为 3。

### `compaction_truncate_enabled` / `compaction_clear_enabled` / `compaction_auto_enabled`

压缩层 A/B/C 的独立开关。默认全部为 `true`。

---

## 输出控制

### `capped_default_max_tokens`

每次 API 调用的默认 `max_tokens`，即使模型可以接受更多也会如此。这是一种槽位预留优化：保持该值较小，可为输入留出更多上下文窗口空间。默认为 8 000。

### `escalated_max_tokens`

生成中途达到上限默认值后的重试预算。默认为 64 000——对大多数当前模型来说接近实际上限。

---

## 错误恢复

### `max_output_tokens_recovery_limit`

当模型不断在 `max_tokens` 处被截断时，放弃前尝试注入多少次"直接继续"。默认为 3。

---

## 工具执行

### `max_tool_concurrency`

只读工具的最大并行执行数。写入和执行工具始终串行运行。默认为 10。

---

## 思考

### `thinking_budget_default`

对于支持扩展思考的模型（Claude Sonnet 4、DeepSeek R1、o1 风格）：模型在可见响应之前可用于推理的 Token 预算。`0` 表示禁用。默认为 10 000。

---

## 会话

### `max_scratchpad_sessions`

保留多少个 scratchpad 目录。较旧的会被 GC 回收。默认为 5。

---

## 校验

加载时的无效值（类型错误、超出枚举范围的字符串、需要正数时却是负整数）会回退到默认值，并向 stderr 记录 WARNING。设置绝不会被静默丢弃——错误的值始终可见。

校验器位于 `pivotcode/settings.py::_VALIDATORS`。

## 相关

- [guides/configuration.md](../guides/configuration.md) — 优先级链，如何在运行时更改设置。
- [reference/cli.md](cli.md) — CLI 标志与最常见的设置一一对应。
- [reference/slash-commands.md](slash-commands.md) — `/settings` 和 `/settings-project`。