"""Anthropic 服务提供者 — 封装官方 Anthropic Python SDK。

将 Anthropic SDK 流事件转换为 Pivot Code 的 ProviderStreamEvent 类型。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

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

# ── 模型能力查询 ─────────────────────────────────────────────────────────

from pivotcode.providers.anthropic_models import lookup_anthropic_model

_CACHE_MARKER = {"type": "ephemeral"}


def _inject_cache_breakpoints(
    system_blocks: list[dict[str, Any]],
    api_tools: list[dict[str, Any]] | None,
    messages: list[dict[str, Any]],
    static_boundary: int,
) -> None:
    """为 Anthropic 提示缓存添加 ``cache_control`` 标记。

    最多使用 4 个断点（Anthropic 的最大值）：
    1. 最后一个工具定义 — 缓存所有工具模式
    2. 最后一个静态系统段 — 缓存工具 + 稳定提示
    3. 最后一个系统段 — 缓存工具 + 完整系统提示
    4. 最后一条助手消息 — 缓存整个对话前缀
    """
    # BP1：最后一个工具定义
    if api_tools:
        api_tools[-1]["cache_control"] = _CACHE_MARKER

    # BP2：最后一个静态系统段
    if system_blocks and static_boundary > 0:
        idx = min(static_boundary, len(system_blocks)) - 1
        system_blocks[idx]["cache_control"] = _CACHE_MARKER

    # BP3：最后一个系统段（动态结尾）
    if system_blocks:
        last = len(system_blocks) - 1
        # 仅在与 BP2 不同时添加（避免浪费断点）
        if static_boundary <= 0 or last != min(static_boundary, len(system_blocks)) - 1:
            system_blocks[last]["cache_control"] = _CACHE_MARKER

    # BP4：最后一条助手消息的最后一个内容块
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, list) and content:
                content[-1]["cache_control"] = _CACHE_MARKER
            break


class AnthropicProvider(LLMProvider):
    """使用官方 Anthropic Python SDK 的 LLM 服务提供者。

    参数
    ----------
    api_key : str | None
        Anthropic API 密钥。如未指定则从 ``ANTHROPIC_API_KEY`` 环境变量读取。
    base_url : str | None
        覆盖默认的 API 基础 URL。
    model : str
        未指定模型时使用的默认模型。
    **client_kwargs
        传递给 ``anthropic.AsyncAnthropic`` 的额外关键字参数。
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "claude-sonnet-4-6",
        **client_kwargs: Any,
    ) -> None:
        import anthropic

        self._model = model
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            **client_kwargs,
        )

    # ── LLMProvider 接口 ──────────────────────────────────────────────────

    def get_model_info(self, model: str | None = None) -> ModelInfo:
        return lookup_anthropic_model(model or self._model)

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
        **kwargs: Any,
    ) -> AsyncGenerator[ProviderStreamEvent, None]:
        """从 Anthropic API 流式获取响应，转换原始事件。

        使用 ``self._client.messages.stream()`` 进行原始事件迭代，
        以便除了文本增量外还能接收 tool_use 事件。
        """
        import anthropic

        resolved_model = model or self._model  # model 参数覆盖构造函数中的模型
        info = self.get_model_info(resolved_model)
        resolved_max_tokens = max_tokens or info.max_output_tokens

        # 构建系统提示块
        system_blocks: list[dict[str, Any]] = [
            {"type": "text", "text": s} for s in system if s
        ]

        # 构建工具定义
        api_tools: list[dict[str, Any]] | None = None
        if tools:
            api_tools = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in tools
            ]

        # 将 OpenAI 格式的消息转换为 Anthropic 格式
        anthropic_messages = _openai_to_anthropic_messages(messages)

        # 提示缓存 — 放置 cache_control 断点
        boundary = kwargs.pop("system_static_boundary", None)
        if boundary is not None:
            _inject_cache_breakpoints(
                system_blocks, api_tools, anthropic_messages, boundary
            )

        # 基础请求参数
        params: dict[str, Any] = {
            "model": resolved_model,
            "messages": anthropic_messages,
            "max_tokens": resolved_max_tokens,
        }
        if system_blocks:
            params["system"] = system_blocks
        if api_tools:
            params["tools"] = api_tools
        if stop_sequences:
            params["stop_sequences"] = stop_sequences

        # 思考模式配置
        use_thinking = (
            thinking is not None
            and thinking.type != "disabled"
            and info.supports_thinking
        )
        if use_thinking:
            assert thinking is not None  # 用于类型收窄
            if thinking.type == "budget" and thinking.budget_tokens is not None:
                params["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": thinking.budget_tokens,
                }
            else:
                # "adaptive" 或未指定 token 数的 budget — 让模型
                # 使用合理的默认预算来决定。
                thinking_default = kwargs.pop("thinking_budget_default", 10_000)
                default_budget = min(thinking_default, resolved_max_tokens // 2)
                params["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": thinking.budget_tokens or default_budget,
                }

        # 合并任何额外的 kwargs（如 metadata、temperature）
        params.update(kwargs)

        try:
            async with self._client.messages.stream(**params) as raw_stream:
                # 跨增量累积工具输入 JSON 的状态
                current_tool_id: str | None = None
                current_tool_name: str | None = None
                accumulated_tool_json: str = ""

                async for event in raw_stream:
                    event_type = event.type

                    # ── message_start ────────────────────────────────────
                    if event_type == "message_start":
                        msg = event.message
                        usage_dict: dict[str, int] | None = None
                        if msg.usage is not None:
                            usage_dict = {
                                "input_tokens": msg.usage.input_tokens,
                                "output_tokens": msg.usage.output_tokens,
                                "cache_read_input_tokens": getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
                                "cache_creation_input_tokens": getattr(msg.usage, "cache_creation_input_tokens", 0) or 0,
                            }
                        yield StreamMessageStart(
                            model=msg.model,
                            request_id=msg.id,
                            usage=usage_dict,
                        )

                    # ── content_block_start ──────────────────────────────
                    elif event_type == "content_block_start":
                        block = event.content_block
                        if block.type == "tool_use":
                            current_tool_id = block.id
                            current_tool_name = block.name
                            accumulated_tool_json = ""
                            yield StreamToolUseStart(
                                id=block.id,
                                name=block.name,
                            )
                        # 文本和思考块只是开始；增量携带实际内容。

                    # ── content_block_delta ──────────────────────────────
                    elif event_type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield StreamTextDelta(text=delta.text)
                        elif delta.type == "thinking_delta":
                            yield StreamThinkingDelta(thinking=delta.thinking)
                        elif delta.type == "input_json_delta":
                            partial = delta.partial_json
                            accumulated_tool_json += partial
                            yield StreamToolUseInputDelta(
                                id=current_tool_id or "",
                                partial_json=partial,
                            )

                    # ── content_block_stop ───────────────────────────────
                    elif event_type == "content_block_stop":
                        # 如果正在累积工具调用，则完成它
                        if current_tool_id is not None:
                            parsed_input: dict[str, Any] = {}
                            if accumulated_tool_json:
                                try:
                                    parsed_input = json.loads(
                                        accumulated_tool_json
                                    )
                                except json.JSONDecodeError:
                                    logger.warning(
                                        "Failed to parse tool input JSON: %s",
                                        accumulated_tool_json,
                                    )
                                    yield StreamError(
                                        error=f"Malformed tool input JSON for {current_tool_name}: {accumulated_tool_json[:200]}",
                                        error_type="api_error",
                                        status_code=None,
                                    )
                            # 防止空 id/name — 发送一个会导致下游
                            # 孤立的 tool_results（下一轮 API 调用会返回 400）。
                            if current_tool_id and current_tool_name:
                                yield StreamToolUseStop(
                                    id=current_tool_id,
                                    name=current_tool_name,
                                    input=parsed_input,
                                )
                            else:
                                logger.warning(
                                    "Dropping tool_use_stop with empty id/name: "
                                    "id=%r, name=%r",
                                    current_tool_id, current_tool_name,
                                )
                            # 重置工具累积状态
                            current_tool_id = None
                            current_tool_name = None
                            accumulated_tool_json = ""

                    # ── message_delta ────────────────────────────────────
                    elif event_type == "message_delta":
                        delta = event.delta
                        usage_out: dict[str, int] | None = None
                        if event.usage is not None:
                            usage_out = {
                                "output_tokens": event.usage.output_tokens,
                            }
                        yield StreamMessageDelta(
                            stop_reason=getattr(delta, "stop_reason", None),
                            usage=usage_out,
                        )

                    # ── message_stop ─────────────────────────────────────
                    elif event_type == "message_stop":
                        yield StreamMessageStop()

        except anthropic.AuthenticationError as exc:
            yield StreamError(
                error=f"Authentication failed: {exc.message}",
                error_type="invalid_request",
                status_code=exc.status_code,
            )
        except anthropic.RateLimitError as exc:
            yield StreamError(
                error=f"Rate limited: {exc.message}",
                error_type="overloaded",
                status_code=exc.status_code,
            )
        except anthropic.APIConnectionError as exc:
            yield StreamError(
                error=f"Connection error: {exc}",
                error_type="api_error",
                status_code=None,
            )
        except anthropic.APIError as exc:
            yield StreamError(
                error=f"API error: {exc.message}",
                error_type="api_error",
                status_code=exc.status_code,
            )
        except Exception as exc:
            logger.exception("Unexpected error during Anthropic streaming")
            yield StreamError(
                error=f"Unexpected error: {exc}",
                error_type="api_error",
                status_code=None,
            )


# ── OpenAI → Anthropic 消息转换 ──────────────────────────────────────────


def _openai_to_anthropic_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """将 OpenAI 格式的消息字典转换为 Anthropic 格式。

    主要转换：
    - 助手消息中的 ``tool_calls`` → ``tool_use`` 内容块
    - ``role: "tool"`` → 用户消息中的 ``tool_result`` 内容块
    - 连续的工具结果合并到一个用户消息中
    """
    result: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")

        if role == "assistant":
            result.append(_convert_assistant_to_anthropic(msg))

        elif role == "tool":
            tool_result_block = {
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id", ""),
                "content": msg.get("content", ""),
            }
            # 将连续的工具结果合并到一个用户消息中
            if (
                result
                and result[-1].get("role") == "user"
                and isinstance(result[-1].get("content"), list)
                and any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in result[-1]["content"]
                )
            ):
                result[-1]["content"].append(tool_result_block)
            else:
                result.append({
                    "role": "user",
                    "content": [tool_result_block],
                })

        elif role == "user":
            result.append({"role": "user", "content": msg.get("content", "")})

        elif role == "system":
            # 系统消息单独处理 — 如存在则传递
            result.append(msg)

        else:
            result.append(msg)

    return result


def _convert_assistant_to_anthropic(msg: dict[str, Any]) -> dict[str, Any]:
    """将 OpenAI 助手消息转换为 Anthropic 内容块格式。"""
    content_blocks: list[dict[str, Any]] = []

    text = msg.get("content")
    if text:
        content_blocks.append({"type": "text", "text": text})

    for tc in msg.get("tool_calls") or []:
        func = tc.get("function", {})
        arguments = func.get("arguments", "{}")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id", ""),
            "name": func.get("name", ""),
            "input": arguments,
        })

    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    return {"role": "assistant", "content": content_blocks}
