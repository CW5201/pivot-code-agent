"""压缩 A 层 —— 截断超出大小的工具结果。

截断那些超过大小限制的单个工具结果。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from pivotcode.messages.types import (
    Message,
    TextBlock,
    ToolResultBlock,
    UserMessage,
)

# 哨兵前缀：让其他压缩阶段（以及调试时）能区分这是合成的截断输出，
# 而非真实的工具数据。
TRUNCATION_SENTINEL = "[ALAN-TRUNCATED]"
REPLACEMENT_MESSAGE = (
    TRUNCATION_SENTINEL
    + " Tool result truncated — {original_size} chars exceeded {max_size} limit."
)


from pivotcode.compact.utils import text_length as _text_length


def _truncate_tool_result_content(
    content: str | list[TextBlock],
    max_chars: int,
) -> str | list[TextBlock]:
    """Replace oversized tool result content with a truncation notice.

    Args:
        content: Original tool result content (string or list of TextBlocks).
        max_chars: Maximum allowed character count.

    Returns:
        A replacement string or single-element TextBlock list.
    """
    original_size = _text_length(content)
    replacement = REPLACEMENT_MESSAGE.format(
        original_size=original_size,
        max_size=max_chars,
    )

    if isinstance(content, str):
        return replacement
    # For list[TextBlock], replace with a single TextBlock
    return [TextBlock(text=replacement)]


def _process_tool_result_block(
    block: ToolResultBlock,
    max_chars: int,
) -> ToolResultBlock:
    """Return a copy of the block, truncating its content if it exceeds max_chars.

    Args:
        block: The tool result block to check.
        max_chars: Maximum allowed character count for the content.

    Returns:
        The original block if within limits, or a truncated copy.
    """
    if _text_length(block.content) <= max_chars:
        return block
    return ToolResultBlock(
        tool_use_id=block.tool_use_id,
        content=_truncate_tool_result_content(block.content, max_chars),
        is_error=block.is_error,
    )


def compaction_truncate_tool_results(
    messages: list[Message],
    *,
    max_chars: int | None = None,
    threshold_tokens: int | None = None,
    settings: dict | None = None,
) -> list[Message]:
    """Enforce per-message budget on tool result size (Layer A).

    If *threshold_tokens* is provided, only runs when estimated token count
    exceeds that threshold. Processes oldest tool results first and stops
    when the estimated token count drops below the threshold (or all
    oversized results have been processed).

    Returns a new list (does not mutate input).
    """
    if max_chars is None:
        max_chars = (settings or {}).get("tool_result_max_chars", 20_000)

    # A 层不做阈值门控：凡是超过 max_chars 的单个结果一律截断。
    # 令牌估算启发式（字符数/4）可能严重低估，导致超大结果绕过阈值门控、
    # 撑爆模型的上下文窗口。

    # 收集超出大小的工具结果索引（最旧的在前 —— 自然顺序）
    oversized: list[tuple[int, int]] = []  # (msg_idx, block_idx)
    for msg_idx, msg in enumerate(messages):
        if not isinstance(msg, UserMessage) or not isinstance(msg.content, list):
            continue
        for block_idx, block in enumerate(msg.content):
            if isinstance(block, ToolResultBlock) and _text_length(block.content) > max_chars:
                oversized.append((msg_idx, block_idx))

    if not oversized:
        return list(messages)

    # 构建修改后的消息列表，从最旧的开始处理
    # 记录哪些消息需要被修改
    messages_to_modify: dict[int, set[int]] = {}
    for msg_idx, block_idx in oversized:
        messages_to_modify.setdefault(msg_idx, set()).add(block_idx)

    result: list[Message] = []
    for msg_idx, msg in enumerate(messages):
        if msg_idx not in messages_to_modify:
            result.append(msg)
            continue

        # 防护：messages_to_modify 里只应是带 list 内容的 UserMessage。
        # 如果上面的筛选逻辑不慎放入了其它类型，就保持该消息不变，
        # 避免破坏状态（在 python -O 下尤其重要，因为 `assert` 会被整体移除）。
        if not (isinstance(msg, UserMessage) and isinstance(msg.content, list)):
            logger.warning(
                "compact_truncate: unexpected message type in modification "
                "set (idx=%d, type=%s); skipping",
                msg_idx, type(msg).__name__,
            )
            result.append(msg)
            continue
        block_indices = messages_to_modify[msg_idx]

        new_content = []
        for block_idx, block in enumerate(msg.content):
            if block_idx in block_indices and isinstance(block, ToolResultBlock):
                new_content.append(_process_tool_result_block(block, max_chars))
            else:
                new_content.append(block)

        # 显式构造一个新的 UserMessage：避免 copy.copy() 的别名隐患
        # （它会与原对象共享 list/dict 字段），并让字段传递一目了然。
        new_msg = UserMessage(
            content=new_content,
            tool_use_result=msg.tool_use_result,
            hide_in_ui=msg.hide_in_ui,
            hide_in_api=msg.hide_in_api,
            source_tool_assistant_uuid=getattr(msg, "source_tool_assistant_uuid", None),
            origin=getattr(msg, "origin", None),
            uuid=msg.uuid,
            timestamp=msg.timestamp,
        )
        result.append(new_msg)

    # 若采用阈值门控，检查是否可在下次调用时提前停止
    # （为简单起见，这里一次性处理所有超大数据 —— 单个截断开销很低，
    # 阈值会在层边界处重新校验）
    return result
