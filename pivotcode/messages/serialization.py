"""消息序列化——将消息 dataclass 转换为 API 字典格式。

两个序列化目标：
- **OpenAI 格式**（``messages_to_openai_dicts``）——通用默认格式。
  由 LiteLLM 以及任何兼容 OpenAI 的提供方使用。
- **Anthropic 格式**（``message_to_anthropic_dict``）——由 AnthropicProvider 使用。

查询循环与压缩过程生成 OpenAI 格式的字典。各提供方在需要时再自行转换。
"""

from __future__ import annotations

import json
from typing import Any

from pivotcode.messages.types import (
    AssistantMessage,
    ImageBlock,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

# ── Anthropic 格式（由 AnthropicProvider 使用） ────────────────────────────


def block_to_anthropic_dict(block: Any) -> dict[str, Any]:
    """将内容块转换为 Anthropic API 字典格式。"""
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    if isinstance(block, ToolResultBlock):
        content = block.content
        if isinstance(content, list):
            content = [block_to_anthropic_dict(b) for b in content]
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": content,
            "is_error": block.is_error,
        }
    if isinstance(block, ThinkingBlock):
        d: dict[str, Any] = {"type": "thinking", "thinking": block.thinking}
        if block.signature:
            d["signature"] = block.signature
        return d
    if isinstance(block, RedactedThinkingBlock):
        return {"type": "redacted_thinking", "data": block.data}
    if isinstance(block, ImageBlock):
        return {"type": "image", "source": block.source}
    return {"type": "unknown"}


def message_to_anthropic_dict(msg: UserMessage | AssistantMessage) -> dict[str, Any]:
    """将一条消息转换为 Anthropic API 字典格式。

    Anthropic 格式：
    - User: ``{"role": "user", "content": [{"type": "tool_result", ...}, ...]}``
    - Assistant: ``{"role": "assistant", "content": [{"type": "tool_use", ...}, ...]}``
    """
    if isinstance(msg, UserMessage):
        if isinstance(msg.content, str):
            return {"role": "user", "content": msg.content}
        return {
            "role": "user",
            "content": [block_to_anthropic_dict(b) for b in msg.content],
        }
    # 处理 AssistantMessage
    return {
        "role": "assistant",
        "content": [block_to_anthropic_dict(b) for b in msg.content],
    }


# ── OpenAI 格式（通用默认） ───────────────────────────────────────


def messages_to_openai_dicts(
    messages: list[UserMessage | AssistantMessage],
) -> list[dict[str, Any]]:
    """将消息列表转换为 OpenAI API 字典格式。

    一条内部消息可能产生多个 OpenAI 字典：
    - 包含 tool_result 块的 UserMessage 会变为多条 ``role: "tool"``
      消息，以及一条可选的、承载剩余文本的 ``role: "user"`` 消息。
    - 包含 tool_use 块的 AssistantMessage 会变为一条带有
      ``content``（文本）和 ``tool_calls``（结构化工具调用）的消息。

    OpenAI 格式：
    - User: ``{"role": "user", "content": "text"}``
    - Assistant: ``{"role": "assistant", "content": "text", "tool_calls": [...]}``
    - Tool result: ``{"role": "tool", "tool_call_id": "...", "content": "..."}``
    """
    result: list[dict[str, Any]] = []

    for msg in messages:
        if isinstance(msg, AssistantMessage):
            result.extend(_assistant_to_openai(msg))
        elif isinstance(msg, UserMessage):
            result.extend(_user_to_openai(msg))
        else:
            # 原样放行未知消息类型
            result.append({"role": "user", "content": str(msg)})

    return result


def _assistant_to_openai(msg: AssistantMessage) -> list[dict[str, Any]]:
    """将 AssistantMessage 转换为 OpenAI 格式。

    将内容拆分为文本（``content``）与工具调用（``tool_calls``）。
    """
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for block in msg.content:
        if isinstance(block, TextBlock):
            text_parts.append(block.text)
        elif isinstance(block, ToolUseBlock):
            tool_calls.append({
                "id": block.id,
                "type": "function",
                "function": {
                    "name": block.name,
                    "arguments": json.dumps(block.input) if isinstance(block.input, dict) else str(block.input),
                },
            })
        # ThinkingBlock、RedactedThinkingBlock——不包含在 OpenAI 格式中

    d: dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(text_parts) if text_parts else None,
    }
    if tool_calls:
        d["tool_calls"] = tool_calls

    return [d]


def _user_to_openai(msg: UserMessage) -> list[dict[str, Any]]:
    """将 UserMessage 转换为 OpenAI 格式。

    包含 tool_result 块的 UserMessage 会被拆分为：
    - ``role: "tool"`` 消息（每个工具结果一条）
    - ``role: "user"`` 消息，承载剩余的文本/图像内容
    """
    if isinstance(msg.content, str):
        return [{"role": "user", "content": msg.content}]

    result: list[dict[str, Any]] = []
    tool_results = [b for b in msg.content if isinstance(b, ToolResultBlock)]
    other_blocks = [b for b in msg.content if not isinstance(b, ToolResultBlock)]

    # 先发出工具结果消息（role=tool）
    for tr in tool_results:
        tr_content = tr.content
        if isinstance(tr_content, list):
            tr_content = "\n".join(
                b.text if isinstance(b, TextBlock) else str(b)
                for b in tr_content
            )
        result.append({
            "role": "tool",
            "tool_call_id": tr.tool_use_id,
            "content": str(tr_content),
        })

    # 发出剩余的 user 内容（文本和/或图像）
    if other_blocks:
        # 检查是否含有图像——若有，则使用内容数组格式
        has_images = any(isinstance(b, ImageBlock) for b in other_blocks)

        if has_images:
            # OpenAI 多模态格式：content 是一个块数组
            content_parts: list[dict[str, Any]] = []
            for b in other_blocks:
                if isinstance(b, TextBlock):
                    content_parts.append({"type": "text", "text": b.text})
                elif isinstance(b, ImageBlock):
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{b.source['media_type']};base64,{b.source['data']}",
                        },
                    })
            result.append({"role": "user", "content": content_parts})
        else:
            # 纯文本格式
            text_parts = []
            for b in other_blocks:
                if isinstance(b, TextBlock):
                    text_parts.append(b.text)
                else:
                    text_parts.append(str(b))
            if text_parts:
                result.append({"role": "user", "content": "\n".join(text_parts)})

    return result
