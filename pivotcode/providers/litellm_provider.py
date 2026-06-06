"""LiteLLM 服务提供者 — 通过统一 API 支持 100+ 种 LLM 服务提供者。

支持 OpenRouter、OpenAI、Anthropic、本地模型（Ollama、vLLM）以及
任何 litellm 支持的服务提供者。

用法::

    from pivotcode.providers.litellm_provider import LiteLLMProvider

    # OpenRouter（免费模型）
    provider = LiteLLMProvider(model="openrouter/mistralai/devstral-2512:free")

    # OpenRouter（付费模型，需要 OPENROUTER_API_KEY 环境变量）
    provider = LiteLLMProvider(model="openrouter/anthropic/claude-sonnet-4")

    # 本地 Ollama
    provider = LiteLLMProvider(model="ollama/llama3.1")

    # OpenAI
    provider = LiteLLMProvider(model="gpt-4o")
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from pivotcode.providers.base import (
    LLMProvider,
    ModelInfo,
    ProviderStreamEvent,
    StreamError,
    StreamMessageDelta,
    StreamMessageStart,
    StreamMessageStop,
    StreamTextDelta,
    StreamThinkingDelta,
    StreamToolUseInputDelta,
    StreamToolUseStart,
    StreamToolUseStop,
    ThinkingConfig,
    ToolSchema,
)

logger = logging.getLogger(__name__)


# 抑制 litellm 的冗长调试日志
def _quiet_litellm() -> None:
    try:
        import litellm
        litellm.suppress_debug_info = True
        litellm.print_verbose = lambda *args, **kwargs: None
        logging.getLogger("LiteLLM").setLevel(logging.WARNING)
        logging.getLogger("LiteLLM Router").setLevel(logging.WARNING)
        logging.getLogger("LiteLLM Proxy").setLevel(logging.WARNING)
    except ImportError:
        pass


_quiet_litellm()

# 常见模型的已知上下文窗口大小（litellm 处理大部分，这是回退）
_KNOWN_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-sonnet-4": 200_000,
    "claude-opus-4": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4.1": 1_000_000,
    "gpt-4.1-mini": 1_000_000,
    "gemini-2.5-flash": 1_000_000,
    "gemini-2.5-pro": 1_000_000,
    "devstral-2512": 128_000,
    "llama3.1": 128_000,
}


class LiteLLMProvider(LLMProvider):
    """使用 litellm 实现多服务提供者支持的 LLM 服务提供者。

    支持任何 litellm 可识别的模型字符串，包括：
    - ``openrouter/anthropic/claude-sonnet-4``
    - ``openrouter/mistralai/devstral-2512:free``
    - ``openrouter/google/gemini-2.5-flash``
    - ``gpt-4o``（OpenAI 直连）
    - ``ollama/llama3.1``（本地）
    - ``anthropic/claude-sonnet-4``（Anthropic 直连）

    API 密钥由 litellm 自动从环境变量解析
    （``OPENROUTER_API_KEY``、``OPENAI_API_KEY``、``ANTHROPIC_API_KEY`` 等）。
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        context_window: int | None = None,
        max_output_tokens: int | None = None,
        extra_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ):
        self._model = model
        self._api_key = api_key
        self._api_base = api_base
        self._context_window_override = context_window
        self._max_output_override = max_output_tokens
        self._extra_kwargs = extra_kwargs or {}

    def get_model_info(self, model: str | None = None) -> ModelInfo:
        """获取模型能力。

        解析顺序：
        1. 构造函数覆盖（context_window、max_output_tokens）
        2. litellm 模型注册表
        3. 我们的已知模型回退表
        4. 安全默认值
        """
        m = model or self._model
        ctx = self._context_window_override
        max_out = self._max_output_override
        supports_thinking = False

        # 尝试 litellm 的注册表（涵盖数百个云模型）
        try:
            import litellm
            info = litellm.get_model_info(m)
            if ctx is None:
                ctx = info.get("max_input_tokens") or info.get("max_tokens")
            if max_out is None:
                max_out = info.get("max_output_tokens")
            supports_thinking = info.get("supports_thinking", False)
        except Exception:
            logger.debug(f"Model '{m}' not found in litellm registry, trying server fallbacks")

        # 回退：查询服务器的 /v1/models 端点（本地服务器）
        if ctx is None and self._api_base:
            ctx = self._query_server_context_window(m)

        # 回退：检查我们的已知模型表以获取上下文窗口大小
        if ctx is None:
            for key, window in _KNOWN_CONTEXT_WINDOWS.items():
                if key in m.lower():
                    ctx = window
                    break

        return ModelInfo(
            context_window=ctx or 200_000,
            max_output_tokens=max_out or 8_192,
            supports_thinking=supports_thinking,
        )

    def _query_server_context_window(self, model: str) -> int | None:
        """查询本地服务器的 /v1/models 或 /api/tags 以获取上下文窗口信息。"""
        import requests as http_requests

        base = self._api_base.rstrip("/")

        # 尝试 OpenAI 兼容的 /v1/models（vLLM、SGLang）
        for endpoint in [f"{base}/models", f"{base.rstrip('/v1')}/v1/models"]:
            try:
                resp = http_requests.get(endpoint, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    for m_info in data.get("data", []):
                        max_len = m_info.get("max_model_len")
                        if max_len:
                            logger.info("Got context window %d from server %s", max_len, endpoint)
                            return max_len
            except Exception:
                continue

        # 尝试 Ollama /api/tags
        try:
            ollama_base = base.replace("/v1", "")
            resp = http_requests.get(f"{ollama_base}/api/tags", timeout=5)
            if resp.status_code == 200:
                for m_info in resp.json().get("models", []):
                    details = m_info.get("details", {})
                    ctx = details.get("context_length")
                    if ctx:
                        logger.info("Got context window %d from Ollama", ctx)
                        return ctx
        except Exception:
            pass

        return None

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: list[str],
        tools: list[ToolSchema],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        thinking: ThinkingConfig | None = None,
        stop_sequences: list[str] | None = None,
        **kwargs,
    ) -> AsyncGenerator[ProviderStreamEvent, None]:
        """从任何 litellm 支持的服务提供者流式获取响应。"""
        try:
            import litellm
        except ImportError:
            yield StreamError(
                error="litellm is not installed. Run: pip install litellm",
                error_type="configuration_error",
            )
            return

        resolved_model = model or self._model
        info = self.get_model_info(resolved_model)
        resolved_max_tokens = max_tokens or info.max_output_tokens
        static_boundary = kwargs.pop("system_static_boundary", None) or 0

        # 构建系统消息（litellm 使用 messages 数组，而不是单独的 system 参数）
        litellm_messages: list[dict[str, Any]] = []
        if system:
            # 使用结构化内容块以便放置 cache_control
            # 标记。LiteLLM 将 cache_control 传递给支持它的
            # 服务提供者（Anthropic、OpenRouter/Anthropic），并为
            # 不支持的服务提供者去除它。
            blocks: list[dict[str, Any]] = []
            for i, s in enumerate(system):
                if not s:
                    continue
                block: dict[str, Any] = {"type": "text", "text": s}
                if i == static_boundary - 1 and static_boundary > 0:
                    block["cache_control"] = {"type": "ephemeral"}
                blocks.append(block)
            if blocks:
                blocks[-1]["cache_control"] = {"type": "ephemeral"}
                litellm_messages.append({"role": "system", "content": blocks})

        # 消息以 OpenAI 格式从查询循环到达 — 直接传递。
        litellm_messages.extend(messages)

        # 提示缓存：标记最后一条助手消息，以便对话前缀在连续 API 调用之间被缓存。
        for msg in reversed(litellm_messages):
            if msg.get("role") == "assistant":
                msg["cache_control"] = {"type": "ephemeral"}
                break

        # 以 OpenAI 格式构建工具（litellm 使用 OpenAI 工具格式）
        litellm_tools = None
        if tools:
            litellm_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in tools
            ]
            litellm_tools[-1]["cache_control"] = {"type": "ephemeral"}

        # 构建补全参数
        completion_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": litellm_messages,
            "max_tokens": resolved_max_tokens,
            "stream": True,
            # 在流中请求使用统计（作为最后一个块到达）
            "stream_options": {"include_usage": True},
            **self._extra_kwargs,
            **kwargs,
        }
        if litellm_tools:
            completion_kwargs["tools"] = litellm_tools
        if stop_sequences:
            completion_kwargs["stop"] = stop_sequences
        if self._api_key:
            completion_kwargs["api_key"] = self._api_key
        if self._api_base:
            completion_kwargs["api_base"] = self._api_base

        # OpenRouter 特定：使用 max_completion_tokens 代替 max_tokens
        if "openrouter" in resolved_model:
            completion_kwargs["max_completion_tokens"] = completion_kwargs.pop("max_tokens")
            # 去除不支持的参数
            completion_kwargs.setdefault("drop_params", True)

        # 产生消息开始
        request_id = str(uuid4())
        yield StreamMessageStart(model=resolved_model, request_id=request_id)

        try:
            response = await litellm.acompletion(**completion_kwargs)

            # 跟踪工具调用和使用量的状态
            current_tool_calls: dict[int, dict[str, Any]] = {}  # 索引 → {id, name, arguments_json}
            final_usage: dict[str, int] | None = None
            stop_emitted = False
            mapped_stop_reason: str | None = None

            async for chunk in response:
                # 从任何块中提取使用量 — 包括没有 choices 的最终
                # 使用量块（空列表）。必须在下面的 choices 守卫之前检查。
                if hasattr(chunk, "usage") and chunk.usage:
                    u = chunk.usage
                    cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
                    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
                    details = getattr(u, "prompt_tokens_details", None)
                    if details and (not cache_write and not cache_read):
                        cache_read = getattr(details, "cached_tokens", 0) or 0
                        cache_write = getattr(details, "cache_write_tokens", 0) or 0
                    final_usage = {
                        "input_tokens": getattr(u, "prompt_tokens", 0) or 0,
                        "output_tokens": getattr(u, "completion_tokens", 0) or 0,
                        "cache_creation_input_tokens": cache_write,
                        "cache_read_input_tokens": cache_read,
                    }

                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue

                finish_reason = chunk.choices[0].finish_reason if chunk.choices else None

                # 文本内容
                if delta.content:
                    yield StreamTextDelta(text=delta.content)

                # 思考/推理（某些服务提供者支持此功能）
                reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if reasoning:
                    yield StreamThinkingDelta(thinking=reasoning)

                # 工具调用（OpenAI 格式：delta.tool_calls 是一个列表）
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index if hasattr(tc, "index") else 0

                        if idx not in current_tool_calls:
                            # 新工具调用开始
                            tool_id = tc.id or f"call_{uuid4().hex[:8]}"
                            tool_name = (tc.function.name if tc.function else None) or ""
                            current_tool_calls[idx] = {
                                "id": tool_id,
                                "name": tool_name,
                                "arguments_json": "",
                                "start_emitted": False,
                            }
                            if tool_name:
                                yield StreamToolUseStart(id=tool_id, name=tool_name)
                                current_tool_calls[idx]["start_emitted"] = True
                        else:
                            # 如果后续获得名称则更新
                            if tc.function and tc.function.name and not current_tool_calls[idx]["start_emitted"]:
                                current_tool_calls[idx]["name"] = tc.function.name
                                yield StreamToolUseStart(
                                    id=current_tool_calls[idx]["id"],
                                    name=tc.function.name,
                                )
                                current_tool_calls[idx]["start_emitted"] = True

                        # 累积参数
                        if tc.function and tc.function.arguments:
                            current_tool_calls[idx]["arguments_json"] += tc.function.arguments
                            yield StreamToolUseInputDelta(
                                id=current_tool_calls[idx]["id"],
                                partial_json=tc.function.arguments,
                            )

                # 检查是否完成 — 完成待处理的工具调用
                if finish_reason and not stop_emitted:
                    for tc_info in current_tool_calls.values():
                        try:
                            parsed_input = json.loads(tc_info["arguments_json"]) if tc_info["arguments_json"] else {}
                        except json.JSONDecodeError:
                            parsed_input = {}
                        yield StreamToolUseStop(
                            id=tc_info["id"],
                            name=tc_info["name"],
                            input=parsed_input,
                        )
                    current_tool_calls.clear()
                    mapped_stop_reason = _map_finish_reason(finish_reason)
                    stop_emitted = True

            # 在循环之后产生带有停止原因和使用量的最终增量，
            # 以便无论块的顺序如何都能捕获使用量。
            if stop_emitted:
                yield StreamMessageDelta(
                    stop_reason=mapped_stop_reason,
                    usage=final_usage,
                )
            yield StreamMessageStop()

        except Exception as e:
            error_str = str(e)
            error_type = "api_error"

            # 分类常见的 litellm 异常
            if "AuthenticationError" in type(e).__name__ or "401" in error_str:
                error_type = "authentication_error"
            elif "RateLimitError" in type(e).__name__ or "429" in error_str:
                error_type = "rate_limit"
            elif (
                "ContextWindowExceededError" in type(e).__name__
                # 更窄的短语列表 — 旧的 `"context" in …` 匹配了
                # 不相关的错误如 "error in context of X"，并且
                # 过度触发了自动压缩。
                or "context length" in error_str.lower()
                or "context_length_exceeded" in error_str.lower()
                or "maximum context" in error_str.lower()
                or "prompt is too long" in error_str.lower()
            ):
                error_type = "prompt_too_long"
            elif "Timeout" in type(e).__name__:
                error_type = "timeout"

            logger.error(f"LiteLLM error ({error_type}): {error_str}")
            yield StreamError(error=error_str, error_type=error_type)


def _map_finish_reason(reason: str | None) -> str:
    """将服务提供者特定的停止原因映射到我们的标准原因。"""
    if reason is None:
        return "end_turn"
    mapping = {
        "stop": "end_turn",
        "end_turn": "end_turn",
        "tool_calls": "tool_use",
        "tool_use": "tool_use",
        "length": "max_tokens",
        "max_tokens": "max_tokens",
        "content_filter": "content_filter",
    }
    return mapping.get(reason, reason)


# ---------------------------------------------------------------------------
# Anthropic → OpenAI 消息格式转换
