# 本地模型

Pivot Code 可以与任何通过 OpenAI 兼容 API 提供的 LLM 配合使用。使用 `--base-url` 指向你的本地服务器。

## 支持的服务器

| 服务器 | Pivot 命令 |
|---|---|
| vLLM | `pivotcode --model openai/<model> --base-url http://localhost:8000/v1` |
| Ollama | `pivotcode --model ollama/<model>` |
| SGLang | `pivotcode --model openai/<model> --base-url http://localhost:8000/v1` |

Ollama 使用 `ollama/` 前缀——LiteLLM 会自动检测 `localhost:11434`，无需 `--base-url`。

## 工具调用

默认情况下，Pivot 使用**原生工具调用**（模型返回结构化的 `tool_calls`）。这适用于支持该功能的服务器（例如带 `--tool-call-parser hermes` 的 vLLM、带工具能力模型的 Ollama）。

对于不支持原生工具调用的模型，请使用**基于文本的工具调用**——Pivot 将工具模式注入系统提示词，并从模型的文本输出中解析工具调用：

```bash
pivotcode --model openai/<model> --base-url http://localhost:8000/v1 --tool-call-format hermes
```

可用格式：`hermes`、`glm`、`pivot`。

## 模型名称格式

LiteLLM 使用模型名称前缀来确定 API 协议：

| 前缀 | 协议 |
|---|---|
| `openai/<name>` | OpenAI 兼容（vLLM、SGLang、任何本地服务器） |
| `ollama/<name>` | Ollama（自动检测 localhost:11434） |
| `anthropic/<name>` | Anthropic API |
| `openrouter/<provider>/<name>` | OpenRouter |

对于本地服务器，使用 `openai/<model>` + `--base-url`。