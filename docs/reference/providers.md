# 后端与供应商

Pivot Code 将"如何与模型通信"（**后端**）与"由哪个服务运行模型"（**供应商**，编码为模型字符串中的前缀）区分开来。

## 一览

共有三种后端：

| 后端 | 设置 | 最适合 | 说明 |
|---|---|---|---|
| `auto` *（非 Claude 模型的默认值）* | `--backend auto` | 除直接使用 Claude 以外的一切：OpenAI、OpenRouter、Gemini、Ollama、vLLM、Bedrock 等 | 通用传输层——底层为 LiteLLM，通过模型字符串前缀支持 50+ 供应商。 |
| `anthropic-native` *（裸 `claude-*` 的默认值）* | `--backend anthropic-native` | Claude 模型 | 直接使用 Anthropic SDK，支持 `cache_control`、原生思考、原生 `tool_use`。 |
| `scripted` | `--backend scripted` | 测试、CI、演示 | 无需网络、零成本、完全确定。 |

供应商——OpenAI、Ollama、OpenRouter 等——**不是**后端。它作为前缀存在于模型字符串内部（LiteLLM 约定）。

通常你根本不需要传 `--backend`——它会从 `--model` 推断。只需传 `--model`。

---

## 后端推断

当未设置 `--backend` 时，会根据模型字符串选择：

- 裸 Claude 名称（`claude-sonnet-4-6`、`claude-opus-4-7` 等）→ `anthropic-native`。
- 其他任何名称 → `auto`（LiteLLM 传输层）。

`anthropic/...` 前缀是经由 LiteLLM 使用 Claude 的显式逃生通道（例如通过 LiteLLM Proxy 路由以实现集中式日志记录）。

| 模型字符串 | 推断的后端 | 使用的 API 密钥 |
|---|---|---|
| `claude-sonnet-4-6` | `anthropic-native` | `ANTHROPIC_API_KEY` |
| `anthropic/claude-sonnet-4-6` | `auto`（LiteLLM → Anthropic） | `ANTHROPIC_API_KEY` |
| `gpt-4o`, `gpt-4.1` | `auto`（LiteLLM → OpenAI） | `OPENAI_API_KEY` |
| `openrouter/...`, `ollama/...`, `gemini/...` 等 | `auto` | 供应商的环境变量 |

---

## `anthropic-native` 后端

**类**: `pivotcode.providers.anthropic_provider.AnthropicProvider`

使用官方的 `anthropic` SDK，让 Pivot 获得 Anthropic 提供的最佳能力：

- **提示词缓存** — 系统提示词被拆分为 4 个缓存块，大幅降低多轮对话成本。
- **扩展思考** — 支持 Claude Sonnet 4 的 `thinking` 模式（预算由 `thinking_budget_default` 设置控制）。
- **原生工具调用** — 结构化的 `tool_use` 块，干净利落的 tool_use → tool_result 关联。

### 模型

`claude-sonnet-4-6`（默认）、`claude-opus-4-7`、`claude-haiku-4-5` 以及更早的 Sonnet/Opus/Haiku 版本。请使用 Anthropic API 文档中的确切模型字符串。

### 配置

```bash
export ANTHROPIC_API_KEY=sk-ant-...
pivotcode --model claude-sonnet-4-6      # backend inferred
# or, explicit:
pivotcode --backend anthropic-native --model claude-sonnet-4-6
```

```python
PivotCodeAgent(model="claude-sonnet-4-6")  # backend inferred
```

### 定价

Pivot 将 Anthropic 的按模型定价硬编码在 `pivotcode/api/cost_tracker.py::ANTHROPIC_PRICING` 中。显示的成本精确到分（包含缓存读取/写入的区分）。

---

## `auto` 后端（LiteLLM 传输层）

**类**: `pivotcode.providers.litellm_provider.LiteLLMProvider`

[LiteLLM](https://docs.litellm.ai/) 的封装，一份配置即可接入 OpenAI、OpenRouter、Gemini、Vertex、Bedrock、Ollama、vLLM、SGLang 以及更多供应商。

### 模型字符串约定

LiteLLM 期望 `provider/model` 格式：

| 供应商 | 示例模型字符串 |
|---|---|
| OpenAI | `gpt-4o`, `gpt-4.1`, `openai/gpt-4o`（显式形式） |
| Anthropic（经由 LiteLLM） | `anthropic/claude-sonnet-4-6` |
| OpenRouter | `openrouter/google/gemini-2.5-pro`, `openrouter/meta-llama/llama-3.3-70b-instruct` |
| Google Gemini（直接） | `gemini/gemini-2.5-pro` |
| Vertex AI | `vertex_ai/gemini-pro` |
| Bedrock | `bedrock/anthropic.claude-3-sonnet-20240229-v1:0` |
| Ollama | `ollama/qwen2.5-coder:7b`（无需 API 密钥） |
| vLLM / SGLang / 任何与 OpenAI 兼容的服务 | `openai/<your-model>` + `--base-url http://localhost:8000/v1` |

### 配置

```bash
# OpenRouter
export OPENROUTER_API_KEY=sk-or-...
pivotcode --model openrouter/google/gemini-2.5-pro

# OpenAI
export OPENAI_API_KEY=sk-...
pivotcode --model gpt-4o

# Local
pivotcode --model openai/my-vllm-model --base-url http://localhost:8000/v1

# DashScope / 阿里云千问 (OpenAI-compatible)
export DASHSCOPE_API_KEY=sk-...
pivotcode --model qwen-plus --base-url https://dashscope.aliyuncs.com/compatible-mode/v1
```

### 哪个环境变量对应哪个供应商

LiteLLM 会读取每个上游供应商的标准环境变量：

| 供应商 | 环境变量 |
|---|---|
| OpenAI | `OPENAI_API_KEY` |
| Anthropic（经由 LiteLLM） | `ANTHROPIC_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| Google Gemini | `GEMINI_API_KEY` 或 `GOOGLE_API_KEY` |
| Vertex AI | `GOOGLE_APPLICATION_CREDENTIALS`（服务账号 JSON） |
| Bedrock | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` + `AWS_REGION_NAME` |
| 本地（Ollama、vLLM、SGLang） | 无——`--base-url` 就足够了 |
| DashScope（Qwen、千问） | `DASHSCOPE_API_KEY` |

### 定价

LiteLLM 自带定价注册表。成本显示读取自 `litellm.model_cost`。未知或未定价的模型会显示 `(estimated)` 或不显示任何内容——参见 [reference/cost.md](cost.md)。

### 工具调用

- **原生工具调用**：大多数现代模型（Claude、GPT-4o、Gemini 2.5、支持函数调用的 Llama 3.3）会被自动检测并以原生方式传递 `tools=[...]`。
- **基于文本的回退**：对于不支持原生函数调用的模型，设置 `--tool-call-format hermes|glm|pivot`。schema 会以文本形式渲染到系统提示词中；输出通过正则解析。详见 [reference/cli.md](cli.md)。

### 基于文本的工具调用

对于不支持原生工具调用的模型，设置 `--tool-call-format` 以启用基于文本的工具调用。Pivot 将工具 schema 注入系统提示词，并从模型的文本输出中解析工具调用：

```bash
--tool-call-format hermes   # Hermes <tool_call> format
--tool-call-format glm      # GLM XML format
--tool-call-format pivot     # Pivot's own format (most portable)
```

当未设置 `--tool-call-format`（默认）时，Pivot 使用原生函数调用。

---

## `scripted` 后端

**类**: `pivotcode.providers.scripted_provider.ScriptedProvider`

面向测试的后端，返回预先设定的响应。无需网络、零成本、完全确定。用于 Pivot 自身的测试套件以及 auto-fix-loop 示例的 `--scripted` 模式。

### 用法

```python
from pivotcode.providers.scripted_provider import (
    ScriptedProvider, text, tool_call, multi_tool_call,
)

provider = ScriptedProvider.from_responses([
    text("Hello!"),
    tool_call("Bash", {"command": "ls"}),
    text("Done."),
])

agent = PivotCodeAgent(backend=provider, permission_mode="yolo")
```

列表中的每个条目都是第 N 次迭代的响应。`text(...)` 返回纯文本响应；`tool_call(...)` 产生一个 tool_use；`multi_tool_call(...)` 在一次响应中产生多个 tool_use 块。

完整的辅助 API（规则、按回合索引的响应等）请参见 `pivotcode/providers/scripted_provider.py`。

---

## 添加自定义后端

所有后端都实现 `pivotcode/providers/base.py` 中的 `LLMProvider` ABC：

```python
from pivotcode.providers.base import LLMProvider, StreamEvent

class MyBackend(LLMProvider):
    async def stream(self, messages, system, tools, *, model, max_tokens, thinking, **kwargs) -> AsyncGenerator[StreamEvent]:
        ...
    def get_model_info(self, model) -> ModelInfo:
        ...
```

然后直接将其注入代理：

```python
agent = PivotCodeAgent(backend=MyBackend(...))
```

`--backend` CLI 标志只认识三个内置后端（`auto`、`anthropic-native`、`scripted`），但构造函数接受任何 `LLMProvider` 实例。

---

## 从旧版本迁移

旧版本提供 `--provider {litellm,anthropic,scripted}`。该标志、`provider` 设置键和 `/provider` 斜杠命令都会作为弃用别名保留一个发布周期：

| 旧版 | 新版 |
|---|---|
| `--provider litellm` | 去掉该标志（或 `--backend auto`） |
| `--provider anthropic` | `--backend anthropic-native`（或直接用 `--model claude-sonnet-4-6`） |
| `--provider scripted` | `--backend scripted` |
| `--provider <other>` | 报错并建议 `--model <other>/<name>`（前缀形式） |

带有 `"provider": "..."` 的旧版 `.pivot/settings.json` 文件会在首次读取时自动迁移。

---

## 相关

- [reference/cli.md](cli.md) — 与后端相关的 CLI 标志。
- [reference/settings.md](settings.md) — 持久化的后端配置。
- [reference/local-models.md](local-models.md) — 详细的本地模型设置。
- [reference/python-api.md](python-api.md) — 以编程方式使用后端。