"""压缩 B 层 —— 清空旧的工具有结果内容。

在保留消息结构的同时，清空旧工具结果中的内容。
"""

from __future__ import annotations

from pivotcode.messages.types import (
    AssistantMessage,
    Message,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from pivotcode.utils.tokens import estimate_message_tokens, rough_token_count

COMPACTABLE_TOOLS = {"Bash", "Read", "Grep", "Glob", "WebSearch", "WebFetch", "Edit", "Write"}
CLEARED_MESSAGE = "[Old tool result content cleared]"




def _estimate_block_tokens(block: ToolResultBlock) -> int:
    """估算一个工具结果块内容的 token 数量。

    Args:
        block: 一个 ToolResultBlock，其内容可能是字符串或 TextBlock 列表。

    Returns:
        使用「约 4 字符/token」的启发式估算出的近似 token 数量。
    """
    if isinstance(block.content, str):
        return rough_token_count(block.content)
    return sum(rough_token_count(tb.text) for tb in block.content)


def _find_tool_name_for_result(messages: list[Message], tool_use_id: str) -> str | None:
    """在消息中向后回溯，查找给定 tool_use_id 对应的工具名称。

    ToolUseBlock（位于 AssistantMessage 中）带有工具名称；而 ToolResultBlock
    （位于 UserMessage 中）只带有 tool_use_id。
    """
    for msg in reversed(messages):
        if not isinstance(msg, AssistantMessage):
            continue
        for block in msg.content:
            if isinstance(block, ToolUseBlock) and block.id == tool_use_id:
                return block.name
    return None


def _collect_tool_result_indices(
    messages: list[Message],
    compactable_tools: set[str],
) -> list[tuple[int, int, str]]:
    """收集所有可压缩工具结果的 (消息索引, 块索引, tool_use_id)。

    按出现顺序（最旧的在前）返回。
    """
    indices: list[tuple[int, int, str]] = []

    for msg_idx, msg in enumerate(messages):
        if not isinstance(msg, UserMessage) or not isinstance(msg.content, list):
            continue
        for block_idx, block in enumerate(msg.content):
            if not isinstance(block, ToolResultBlock):
                continue
            tool_name = _find_tool_name_for_result(messages, block.tool_use_id)
            if tool_name is not None and tool_name in compactable_tools:
                indices.append((msg_idx, block_idx, block.tool_use_id))

    return indices


def compaction_clear_tool_results(
    messages: list[Message],
    *,
    keep_recent: int | None = None,
    compactable_tools: set[str] = COMPACTABLE_TOOLS,
    threshold_tokens: int | None = None,
    settings: dict | None = None,
) -> tuple[list[Message], int]:
    """清空旧工具结果内容以节省 token（B 层）。

    如果提供了 *threshold_tokens*，则仅在估算 token 数超过该阈值时才运行。
    先处理最旧的工具结果，在估算 token 数降到阈值以下（或所有
    可清空的结果都已处理完毕）时停止。

    返回 (new_messages, tokens_saved)。
    仅清空来自可压缩工具的结果。
    保留最后 `keep_recent` 个工具结果不被改动。
    """
    if keep_recent is None:
        keep_recent = (settings or {}).get("compact_clear_keep_recent", 10)

    # 阈值闸门：低于阈值则跳过
    if threshold_tokens is not None:
        current_tokens = estimate_message_tokens(messages)
        if current_tokens < threshold_tokens:
            return list(messages), 0

    # 查找所有可压缩工具结果所在的位置
    tool_result_indices = _collect_tool_result_indices(messages, compactable_tools)

    if not tool_result_indices:
        return list(messages), 0

    # 确定要清空哪些（除最后 keep_recent 个之外的全部）
    num_to_clear = max(0, len(tool_result_indices) - keep_recent)
    if num_to_clear == 0:
        return list(messages), 0

    # 若设置了阈值闸门，则先处理最旧的，降到阈值以下即停止
    if threshold_tokens is not None:
        current_tokens = estimate_message_tokens(messages)
        clearable = tool_result_indices[:num_to_clear]
        indices_to_clear_list: list[tuple[int, int]] = []
        running_saved = 0
        for msg_idx, block_idx, _ in clearable:
            msg = messages[msg_idx]
            if not isinstance(msg, UserMessage) or not isinstance(msg.content, list):
                continue
            block = msg.content[block_idx]
            if isinstance(block, ToolResultBlock):
                old_tokens = _estimate_block_tokens(block)
                new_tokens = rough_token_count(CLEARED_MESSAGE)
                saved = max(0, old_tokens - new_tokens)
                indices_to_clear_list.append((msg_idx, block_idx))
                running_saved += saved
                if current_tokens - running_saved < threshold_tokens:
                    break
        indices_to_clear = set(indices_to_clear_list)
    else:
        indices_to_clear = set(
            (msg_idx, block_idx) for msg_idx, block_idx, _ in tool_result_indices[:num_to_clear]
        )

    # 记录哪些消息需要修改
    messages_to_modify: dict[int, set[int]] = {}
    for msg_idx, block_idx in indices_to_clear:
        messages_to_modify.setdefault(msg_idx, set()).add(block_idx)

    tokens_saved = 0
    new_messages: list[Message] = []

    for msg_idx, msg in enumerate(messages):
        if msg_idx not in messages_to_modify:
            new_messages.append(msg)
            continue

        # 该消息含有需要清空的工具结果
        if not isinstance(msg, UserMessage) or not isinstance(msg.content, list):
            continue

        block_indices_to_clear = messages_to_modify[msg_idx]
        new_content = []

        for block_idx, block in enumerate(msg.content):
            if block_idx not in block_indices_to_clear or not isinstance(block, ToolResultBlock):
                new_content.append(block)
                continue

            # 在清空前计算节省的 token 数
            old_tokens = _estimate_block_tokens(block)
            new_tokens = rough_token_count(CLEARED_MESSAGE)
            tokens_saved += max(0, old_tokens - new_tokens)

            # 替换为清空后的版本
            new_content.append(
                ToolResultBlock(
                    tool_use_id=block.tool_use_id,
                    content=CLEARED_MESSAGE,
                    is_error=block.is_error,
                )
            )

        # 显式构造：避免与 `msg` 的mutable 字段产生别名共享。
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
        new_messages.append(new_msg)

    return new_messages, tokens_saved
