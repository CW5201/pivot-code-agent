# CLI 参数

每个参数都遵循相同的优先级链：

> **CLI 标志 > `.pivot/settings.json`（按项目）> 内置默认值**

因此，任何可以在命令行上传入的内容，也可以在每个项目的 `.pivot/settings.json` 中设置一次，只有当你想要覆盖该默认值时，才需要使用标志。

运行 `pivotcode --help` 可快速查看此表格的精简版本。

## 模型与后端

| 标志 | 描述 | 默认值 |
|---|---|---|
| `--model` | 模型名称。裸名称（`claude-sonnet-4-6`、`gpt-4o`）或 LiteLLM 风格的 `provider/model` 前缀（`ollama/llama3.1`、`openrouter/google/gemini-2.5-pro`、`gemini/gemini-2.5-flash`、`anthropic/claude-sonnet-4-6`）。 | `claude-sonnet-4-6` |
| `--backend` | 传输方式（高级——未设置时从 `--model` 推断）。可选 `auto`（通用 LiteLLM 传输）、`anthropic-native`（带 `cache_control` 的 Anthropic 直连 SDK、原生思考、原生 `tool_use`）或 `scripted`（测试）。 | 推断 |
| `--api-key` | API 密钥。若省略，则从供应商常用的环境变量中读取（`ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`OPENROUTER_API_KEY`、…）。 | （环境变量） |
| `--base-url` | 覆盖 API 基础 URL。为本地 OpenAI 兼容服务器设置此项，例如 `http://localhost:8000/v1`。参见 [`local-models.md`](local-models.md)。 | *（供应商默认值）* |
| `--provider` | **已弃用**的 `--backend` 别名。接受旧值 `litellm`（→ `auto`）、`anthropic`（→ `anthropic-native`）、`scripted`（→ `scripted`）；其他值会产生友好的错误提示，建议正确的 `--model` 形式（例如 `--provider ollama` → 使用 `--model ollama/<name>`）。 | *（未设置）* |

### 后端推断

当 `--backend` 未设置时，根据模型字符串选择：

- 裸 Claude 名称（例如 `claude-sonnet-4-6`、`claude-opus-4-7`）→ `anthropic-native`。解锁 `cache_control`、原生思考与原生 `tool_use`。
- 其他任何名称（`gpt-4o`、`ollama/llama3.1`、`openrouter/...`、`gemini/...`、`anthropic/claude-...`）→ `auto`（LiteLLM 传输）。

`anthropic/...` 前缀是通过 LiteLLM 使用 Claude 的显式逃生口（例如通过 LiteLLM Proxy 路由以实现集中式日志记录）。

### 工具调用格式

默认情况下，Pivot 使用供应商原生的工具调用。如果你的供应商/模型不支持该功能，请使用 `--tool-call-format` 指定基于文本的格式（详情参见 [`local-models.md`](local-models.md)）。

| 标志 | 描述 | 默认值 |
|---|---|---|
| `--tool-call-format` | 面向不支持原生工具调用的模型的基于文本的工具调用格式：`hermes`、`glm` 或 `pivot`。 | *（无——使用原生）* |

## 会话行为

| 标志 | 描述 | 默认值 |
|---|---|---|
| `--permission-mode` | `safe`（每个工具都询问）、`edit`（写入 + 执行时询问）、`yolo`（允许一切）。 | `edit` |
| `--max-iterations-per-turn` | 每条用户消息的模型调用硬上限。 | 无限制 |
| `--max-output-tokens` | 每次调用的输出令牌上限。恢复时可在内部升级至 `escalated_max_tokens`。 | *（供应商默认值）* |
| `--memory` | 记忆模式：`off`（默认）、`on`、`intensive`。 | `off` |
| `--verbose` | 启用调试级日志记录。 | `false` |

## 会话恢复

| 标志 | 描述 |
|---|---|
| `--resume` | 恢复当前工作目录中最近的会话。 |
| `--continue [prefix]` | 不带参数：列出最近的会话。带会话 ID 前缀：恢复指定会话。 |

## 模式

| 标志 | 描述 |
|---|---|
| *（无——默认）* | 交互式 CLI 模式。 |
| `--gui` | 启动本地浏览器图形界面。 |
| `--print PROMPT` | 非交互：发送一条提示词，打印最终答案，然后退出。 |

## 实用工具

| 标志 | 描述 |
|---|---|
| `--version` | 显示已安装版本并退出。 |
| `--help` | 显示内置参数参考并退出。 |

---

## 通过 `.pivot/settings.json` 设置

上述每个标志都映射到项目 `.pivot/settings.json` 中的一个键。该文件在首次运行时自动生成并带有合理的默认值，当未来版本添加新选项时会被**自动迁移**——你已有的值会被保留。

示例：

```json
{
  "backend": "anthropic-native",
  "model": "claude-sonnet-4-6",
  "permission_mode": "edit",
  "memory": "off"
}
```

带有 `"provider": "litellm"` / `"provider": "anthropic"` / `"provider": "scripted"` 的旧文件在首次读取时会被自动迁移为 `"backend"`（会记录一条一行的信息通知）。

## 运行时修改

使用 `/settings <key> <value>` 斜杠命令（参见 [`slash-commands.md`](slash-commands.md)）在会话中途更改设置。后端相关的更改（`backend`、`model`、`api_key`、`base_url`）会触发创建全新的 `LLMProvider`；其他设置会在下一轮生效。更改 `model` 还会根据上述规则重新推断后端。