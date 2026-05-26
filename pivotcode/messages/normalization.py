"""面向 API 提交的消息规范化。

将内部消息列表（可能包含系统消息、附件、虚拟的仅展示消息以及进度事件）
转换为 Claude API 所要求的严格 user/assistant 交替格式。
"""

from __future__ import annotations

import logging
from copy import deepcopy

from pivotcode.messages.types import (
    AssistantMessage,
    AttachmentMessage,
    Message,
    ProgressMessage,
    SystemMessage,
    SystemMessageSubtype,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserContentBlock,
    UserMessage,
)

logger = logging.getLogger(__name__)


# ── 公开 API ──────────────────────────────────────────────────────────────


def normalize_messages_for_api(
    messages: list[Message],
) -> list[UserMessage | AssistantMessage]:
    """将内部消息转换为可供 API 使用的格式。

    步骤：
        1. 过滤掉虚拟消息（``hide_in_api=True``）。
        2. 过滤掉系统消息——但 ``local_command`` 除外，它会被转换为
           UserMessage。
        3. 将附件消息转换为携带附件内容（以文本形式）的 UserMessage。
        4. 过滤掉进度消息。
        5. 合并连续的同角色消息（Bedrock 兼容所需，并用于满足 API 的
           交替约束）。
        6. 只返回 UserMessage 和 AssistantMessage 实例。
    """
    result: list[UserMessage | AssistantMessage] = []

    for msg in messages:
        converted = _convert_message(msg)
        if converted is None:
            continue

        # 合并连续的同角色消息
        if result and _same_role(result[-1], converted):
            if isinstance(result[-1], UserMessage) and isinstance(converted, UserMessage):
                result[-1] = merge_user_messages(result[-1], converted)
            elif isinstance(result[-1], AssistantMessage) and isinstance(converted, AssistantMessage):
                result[-1] = _merge_assistant_messages(result[-1], converted)
        else:
            result.append(converted)

    # 丢弃那些 tool_use_id 在任何之前的助手消息中都没有对应
    # tool_use 的孤儿 tool_result 块。在合并步骤之后，有可能混入
    # 孤儿块（配对辅助函数运行在合并之前）。任何孤儿 tool_result 都会导致
    # API 以 400 拒绝该请求。
    _drop_orphan_tool_results(result)

    return result


def _drop_orphan_tool_results(
    messages: list[UserMessage | AssistantMessage],
) -> None:
    """删除那些从未出现在此前助手 tool_use 中的 tool_result 块。
    就地修改 ``messages``。
    """
    known_tool_use_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    known_tool_use_ids.add(block.id)
            continue
        # UserMessage：丢弃孤儿 tool_result
        if not isinstance(msg.content, list):
            continue
        kept = []
        dropped = 0
        for block in msg.content:
            if (
                isinstance(block, ToolResultBlock)
                and block.tool_use_id
                and block.tool_use_id not in known_tool_use_ids
            ):
                dropped += 1
                continue
            kept.append(block)
        if dropped:
            logger.warning(
                "Dropped %d orphan tool_result block(s) during normalization",
                dropped,
            )
            msg.content = kept


def merge_user_messages(a: UserMessage, b: UserMessage) -> UserMessage:
    """将两个连续的用户消息合并为一个。

    内容列表会被拼接。如果任一消息的内容是普通字符串，
    则首先将其包装为 :class:`TextBlock`。
    """
    merged = deepcopy(a)
    merged.content = _to_content_list(a.content) + _to_content_list(b.content)
    return merged


def get_text_content(message: UserMessage | AssistantMessage) -> str:
    """从消息中提取拼接后的文本内容。

    返回所有 :class:`TextBlock` 项（针对列表内容）的文本拼接结果，
    或原始字符串（针对字符串内容）。
    """
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
    return "".join(parts)


# ── 内部辅助函数 ────────────────────────────────────────────────────────


def _convert_message(msg: Message) -> UserMessage | AssistantMessage | None:
    """转换或过滤单条消息。

    当消息应当被完全丢弃时返回 ``None``。
    """
    # 1. 过滤进度消息
    if isinstance(msg, ProgressMessage):
        return None

    # 2. 过滤虚拟消息
    if isinstance(msg, (UserMessage, AssistantMessage)) and msg.hide_in_api:
        return None

    # 3. 处理系统消息
    if isinstance(msg, SystemMessage):
        return _convert_system_message(msg)

    # 4. 处理附件消息
    if isinstance(msg, AttachmentMessage):
        return _convert_attachment_message(msg)

    # 5. 原样放行用户和助手消息
    if isinstance(msg, (UserMessage, AssistantMessage)):
        return msg

    return None


def _convert_system_message(msg: SystemMessage) -> UserMessage | None:
    """在需要保留时转换 SystemMessage，否则丢弃它。

    只有 ``local_command`` 系统消息会被转换为用户消息，
    以便模型能看到其输出。所有其他系统消息（压缩边界、信息性消息等）
    都会被过滤掉。
    """
    if msg.subtype == SystemMessageSubtype.LOCAL_COMMAND:
        return UserMessage(
            content=msg.content,
            uuid=msg.uuid,
            timestamp=msg.timestamp,
            hide_in_ui=True,
        )
    return None


def _convert_attachment_message(msg: AttachmentMessage) -> UserMessage:
    """将 AttachmentMessage 转换为 UserMessage。

    附件内容会以一个文本块的形式呈现，以便模型能够读取。
    会前置一小段头部，让模型了解附件的类型上下文。
    """
    attachment = msg.attachment
    if attachment.content:
        text = f"[Attachment: {attachment.type}]\n{attachment.content}"
    else:
        text = f"[Attachment: {attachment.type}]"
    return UserMessage(
        content=text,
        uuid=msg.uuid,
        timestamp=msg.timestamp,
        hide_in_ui=True,
    )


def _merge_assistant_messages(
    a: AssistantMessage, b: AssistantMessage,
) -> AssistantMessage:
    """合并两个连续的助手消息。"""
    merged = deepcopy(a)
    merged.content = list(a.content) + list(b.content)
    return merged


def _same_role(
    a: UserMessage | AssistantMessage,
    b: UserMessage | AssistantMessage,
) -> bool:
    """检查两条消息是否具有相同的角色。"""
    return type(a) is type(b)


def _to_content_list(
    content: str | list[UserContentBlock],
) -> list[UserContentBlock]:
    """确保 content 是一个内容块列表。"""
    if isinstance(content, str):
        return [TextBlock(text=content)]
    return list(content)
