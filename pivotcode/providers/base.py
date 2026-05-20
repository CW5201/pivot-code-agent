"""LLM 服务提供者抽象层。

代理循环仅与 LLMProvider 交互。
每个实现将其原生 API 格式转换为 StreamEvents。
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

# ── 流事件 — 由 LLMProvider.stream() 产生 ─────────────────────────────


@dataclass
class StreamTextDelta:
    """增量文本内容。"""
    text: str
    type: str = "text_delta"


@dataclass
class StreamToolUseStart:
    """工具调用的开始。"""
    id: str
    name: str
    type: str = "tool_use_start"


@dataclass
class StreamToolUseInputDelta:
    """增量工具输入 JSON。"""
    id: str
    partial_json: str
    type: str = "input_json_delta"


@dataclass
class StreamToolUseStop:
    """工具调用结束，包含完整输入。"""
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use_stop"


@dataclass
class StreamThinkingDelta:
    """增量思考内容。"""
    thinking: str
    type: str = "thinking_delta"


@dataclass
class StreamMessageStart:
    """来自模型的新消息的开始。"""
    model: str
    request_id: str | None = None
    usage: dict[str, int] | None = None
    type: str = "message_start"


@dataclass
class StreamMessageDelta:
    """消息级别的元数据更新（stop_reason、usage）。"""
    stop_reason: str | None = None
    usage: dict[str, int] | None = None
    type: str = "message_delta"


@dataclass
class StreamMessageStop:
    """消息结束。"""
    type: str = "message_stop"


@dataclass
class StreamError:
    """流式传输过程中的错误。"""
    error: str
    error_type: str = "api_error"  # 'api_error', 'overloaded', 'invalid_request'
    status_code: int | None = None
    type: str = "error"


# 所有流事件的联合类型
ProviderStreamEvent = (
    StreamTextDelta
    | StreamToolUseStart
    | StreamToolUseInputDelta
    | StreamToolUseStop
    | StreamThinkingDelta
    | StreamMessageStart
    | StreamMessageDelta
    | StreamMessageStop
    | StreamError
)


# ── 模型与工具配置 ──────────────────────────────────────────────────────


@dataclass
class ModelInfo:
    """模型能力信息。"""
    context_window: int = 200_000
    max_output_tokens: int = 8_192
    supports_thinking: bool = False


@dataclass
class ToolSchema:
    """发送给服务提供者的工具定义。"""
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ThinkingConfig:
    """思考模式配置。"""
    type: str = "disabled"  # 'disabled', 'adaptive', 'budget'
    budget_tokens: int | None = None


# ── 抽象基类 ─────────────────────────────────────────────────────────────


class LLMProvider(ABC):
    """任何 LLM 后端的抽象接口。

    实现必须将其原生流式 API 转换为 ``ProviderStreamEvent`` 数据类序列，
    以便代理循环可以保持与提供者无关。
    """

    @abstractmethod
    async def stream(
        self,
        messages: list[dict[str, Any]],  # API-format messages
        system: list[str],
        tools: list[ToolSchema],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        thinking: ThinkingConfig | None = None,
        stop_sequences: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ProviderStreamEvent, None]:
        """从 LLM 流式获取响应。

        按以下顺序产生 ``ProviderStreamEvent`` 实例::

            StreamMessageStart          # 始终首先：模型名称、请求 ID
            StreamTextDelta*            # 零个或多个文本块
            StreamThinkingDelta*        # 零个或多个思考块（如果模型支持）
            StreamToolUseStart          # 开始工具调用（id + name）
            StreamToolUseInputDelta*    # 工具输入的部分 JSON
            StreamToolUseStop           # 结束工具调用，包含解析后的输入字典
            ... (可能还有更多文本/工具块) ...
            StreamMessageDelta          # 始终接近最后：stop_reason + 最终 usage
            StreamMessageStop           # 始终最后：表示消息结束

        任何点出错时，产生 ``StreamError`` 并返回。

        参数:
            messages: 对话历史（字典列表，格式取决于提供者）。
            system: 系统提示段落（提供者按需拼接）。
            tools: 模型可调用的工具定义。
            model: 覆盖提供者的默认模型。
            max_tokens: 此请求的最大输出 token 数。
            thinking: 扩展思考配置。
            stop_sequences: 自定义停止序列。
        """
        ...  # pragma: no cover

    @abstractmethod
    def get_model_info(self, model: str | None = None) -> ModelInfo:
        """返回模型的能力信息。"""
        ...  # pragma: no cover

