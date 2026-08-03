"""自动压缩 —— 当上下文过大时，通过摘要来压缩对话。

压缩层级体系中的 C 层。它会发起一次无工具调用的 LLM 调用，
生成 ``<analysis>`` + ``<summary>`` 响应；该摘要会替换边界前的历史记录。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pivotcode.compact.prompt import (
    format_compact_summary,
    get_compact_prompt,
    get_post_compact_message,
    get_post_compact_notification,
)
from pivotcode.messages.factory import (
    create_compact_boundary_message,
    create_user_message,
)
from pivotcode.messages.normalization import normalize_messages_for_api
from pivotcode.messages.serialization import messages_to_openai_dicts
from pivotcode.messages.types import (
    AssistantMessage,
    Message,
    SystemMessage,
    UserMessage,
    get_messages_after_compact_boundary,
)
from pivotcode.providers.base import StreamError, StreamTextDelta
from pivotcode.utils.tokens import (
    estimate_message_tokens,
    rough_token_count,
)

logger = logging.getLogger(__name__)


@dataclass
class CompactionResult:
    summary_messages: list[UserMessage]
    boundary_message: SystemMessage
    pre_compact_token_count: int
    post_compact_token_count: int




# ---------------------------------------------------------------------------
# PTL（提示过长）重试支持
# ---------------------------------------------------------------------------


def truncate_middle_for_ptl(
    messages: list[UserMessage | AssistantMessage],
) -> list[UserMessage | AssistantMessage] | None:
    """从中间裁掉约 20%，保留开头和结尾。

    系统提示是独立参数，永远不会被改动。
    最前面的若干消息（原始上下文）和最后的消息
    （近期工作）会被保留，中间部分被裁掉。

    如果消息数量过少无法裁切，则返回 None。
    """
    if len(messages) <= 4:
        return None
    n = len(messages)
    cut_size = max(1, n // 5)  # ~20% of messages
    cut_start = (n - cut_size) // 2  # center the cut
    cut_end = cut_start + cut_size
    return messages[:cut_start] + messages[cut_end:]


# ---------------------------------------------------------------------------
# 主压缩入口
# ---------------------------------------------------------------------------


async def compaction_auto(
    messages: list[Message],
    provider: Any,  # LLMProvider
    *,
    model: str | None = None,
    custom_instructions: str | None = None,
    session_id: str | None = None,
    memory_mode: str = "on",
    settings: dict | None = None,
) -> CompactionResult | None:
    """通过 LLM 摘要来压缩对话（C 层）。

    1. 构建压缩系统提示（替换用，而非主提示）
    2. 用 9 段模板构建压缩用户消息
    3. 对消息进行归一化以便调用 API
    4. PTL 重试循环：调用 provider，若提示过长则截断后重试
    5. 通过 format_compact_summary 提取摘要
    6. 构建包含边界、摘要与通知的 CompactionResult

    若所有重试后压缩仍失败，则返回 None。
    """

    # 获取自上次压缩边界之后的消息
    relevant_messages = get_messages_after_compact_boundary(messages)

    pre_compact_token_count = estimate_message_tokens(relevant_messages)
    logger.info(
        "Starting compaction: %d messages, ~%d tokens",
        len(relevant_messages),
        pre_compact_token_count,
    )

    # 1. 压缩前先预截断过大的工具结果
    # 否则，单个巨大的工具结果（例如 216K 字符）会被纳入压缩请求，
    # 超出 LLM 的上下文窗口。

    from pivotcode.compact.compact_truncate import compaction_truncate_tool_results
    truncated_messages = compaction_truncate_tool_results(
        relevant_messages, settings=settings,
    )

    # 2. 构建压缩系统提示（替换用，而非追加）
    compact_system = ["You are a helpful AI assistant tasked with summarizing conversations."]

    # 3. 构建压缩用户消息
    compact_prompt = get_compact_prompt(custom_instructions)

    # 4. 对消息进行归一化以便调用 API
    api_messages = normalize_messages_for_api(truncated_messages)
    api_messages_dicts = messages_to_openai_dicts(api_messages)

    # 将压缩提示作为最后一条用户消息追加
    api_messages_dicts.append({"role": "user", "content": compact_prompt})

    # 4. PTL 重试循环
    s = settings or {}
    max_ptl_retries = s.get("max_compact_ptl_retries", 3)
    compact_max_output_tokens = s.get("compact_max_output_tokens", 20_000)

    response_text = ""
    kwargs: dict[str, Any] = {}
    if model is not None:
        kwargs["model"] = model

    for attempt in range(max_ptl_retries + 1):
        response_text = ""
        try:
            async for event in provider.stream(
                api_messages_dicts,
                compact_system,
                tools=[],
                max_tokens=compact_max_output_tokens,
                **kwargs,
            ):
                if isinstance(event, StreamTextDelta):
                    response_text += event.text
                elif isinstance(event, StreamError):
                    error_msg = event.error.lower() if event.error else ""
                    if "prompt" in error_msg and "too long" in error_msg:
                        raise _PromptTooLongError(event.error)
                    # 其他错误：记录日志并失败
                    logger.warning("Compaction stream error: %s", event.error)
                    response_text = ""
                    break

            if response_text.strip():
                break  # Success

        except _PromptTooLongError:
            if attempt >= max_ptl_retries:
                logger.error(
                    "Compaction failed: prompt too long after %d retries", max_ptl_retries
                )
                return None
            # 截断并重试
            # 移除压缩提示（最后一个元素），截断后重新追加
            # 重新归一化为 UserMessage/AssistantMessage 以便截断
            truncated = truncate_middle_for_ptl(api_messages)
            if truncated is None:
                logger.error("Too few messages to truncate for PTL retry")
                return None
            api_messages = truncated
            api_messages_dicts = messages_to_openai_dicts(api_messages)
            api_messages_dicts.append({"role": "user", "content": compact_prompt})
            logger.info(
                "PTL retry %d: truncated to %d messages",
                attempt + 1,
                len(api_messages),
            )
            continue

        except Exception as e:
            logger.error("Compaction failed with exception: %s", e)
            return None

    if not response_text.strip():
        logger.error("LLM returned empty response for compaction")
        return None

    # 5. 提取摘要
    summary = format_compact_summary(response_text)
    logger.info(
        "Compaction complete: summary is ~%d tokens",
        rough_token_count(summary),
    )

    # 此处无法获取转录文件路径（我们没有 cwd）。
    # 压缩后的通知将省略该路径。
    transcript_path: str | None = None

    # 6. 构建边界消息
    boundary_message = create_compact_boundary_message(
        trigger="auto",
        pre_tokens=pre_compact_token_count,
        messages_summarized=len(relevant_messages),
    )

    # 构建摘要用户消息（带压缩后包装）
    post_compact_text = get_post_compact_message(
        response_text,
        transcript_path=transcript_path,
        memory_mode=memory_mode,
    )
    summary_message = create_user_message(
        post_compact_text,
        is_compact_summary=True,
    )

    # 构建通知消息（给模型用的系统提醒）
    notification_text = get_post_compact_notification(memory_mode=memory_mode)
    notification_message = create_user_message(
        notification_text,
        hide_in_ui=True,
    )

    # 计算压缩后的 token 数量
    post_compact_messages = [boundary_message, summary_message, notification_message]
    post_compact_token_count = estimate_message_tokens(post_compact_messages)

    return CompactionResult(
        summary_messages=[summary_message, notification_message],
        boundary_message=boundary_message,
        pre_compact_token_count=pre_compact_token_count,
        post_compact_token_count=post_compact_token_count,
    )


class _PromptTooLongError(Exception):
    """内部哨兵类，用于表示压缩过程中提示过长的错误。"""
