"""消息工厂函数。

为所有消息类型提供便捷的构造函数，处理默认值与常见模式
（例如合成错误消息、中断消息）。
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pivotcode.messages.types import (
    AssistantMessage,
    Attachment,
    AttachmentMessage,
    CompactClearMetadata,
    CompactMetadata,
    ImageBlock,
    MessageOrigin,
    SystemMessage,
    SystemMessageSubtype,
    TextBlock,
    ToolResultBlock,
    Usage,
    UserMessage,
)

# ── 哨兵常量 ──────────────────────────────────────────────────────

SYNTHETIC_MODEL = "<synthetic>"

INTERRUPT_MESSAGE = "[Request interrupted by user]"
INTERRUPT_MESSAGE_FOR_TOOL_USE = "[Request interrupted by user for tool use]"

CANCEL_MESSAGE = (
    "The user doesn't want to take this action right now. "
    "STOP what you are doing and wait for the user to tell you how to proceed."
)
REJECT_MESSAGE = (
    "The user doesn't want to proceed with this tool use. The tool use was rejected. "
    "STOP what you are doing and wait for the user to tell you how to proceed."
)


# ── 辅助函数 ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ── 用户消息 ─────────────────────────────────────────────────────────────────


def create_user_message(
    content: str | list,
    *,
    hide_in_ui: bool = False,
    hide_in_api: bool = False,
    is_compact_summary: bool = False,
    tool_use_result: Any = None,
    source_tool_assistant_uuid: UUID | None = None,
    permission_mode: str | None = None,
    origin: MessageOrigin | None = None,
    uuid: UUID | None = None,
    timestamp: str | None = None,
) -> UserMessage:
    """使用合理的默认值创建一个 UserMessage。"""
    return UserMessage(
        content=content,
        uuid=uuid or uuid4(),
        timestamp=timestamp or _now_iso(),
        hide_in_ui=hide_in_ui,
        hide_in_api=hide_in_api,
        is_compact_summary=is_compact_summary,
        tool_use_result=tool_use_result,
        source_tool_assistant_uuid=source_tool_assistant_uuid,
        permission_mode=permission_mode,
        origin=origin,
    )


def create_user_interruption_message(*, tool_use: bool = False) -> UserMessage:
    """创建一个表示请求已被中断的用户消息。

    Args:
        tool_use: 如果为 True，表示中断是为了执行工具调用。
    """
    text = INTERRUPT_MESSAGE_FOR_TOOL_USE if tool_use else INTERRUPT_MESSAGE
    return create_user_message(text)


def create_tool_result_message(
    tool_use_id: str,
    content: str,
    *,
    is_error: bool = False,
    source_tool_assistant_uuid: UUID | None = None,
) -> UserMessage:
    """创建一个包含单个 ToolResultBlock 的 UserMessage。

    这是将工具执行结果反馈给模型的方式。
    """
    result_block = ToolResultBlock(
        tool_use_id=tool_use_id,
        content=content,
        is_error=is_error,
    )
    return create_user_message(
        [result_block],
        source_tool_assistant_uuid=source_tool_assistant_uuid,
    )


# ── 助手消息 ──────────────────────────────────────────────────────


def create_assistant_message(
    content: str | list,
    *,
    usage: Usage | None = None,
    hide_in_api: bool = False,
) -> AssistantMessage:
    """创建一个 AssistantMessage。

    如果 *content* 是纯字符串，则将其包装在单个 TextBlock 中。
    """
    if isinstance(content, str):
        content = [TextBlock(text=content)]
    return AssistantMessage(
        content=content,
        usage=usage or Usage(),
        hide_in_api=hide_in_api,
    )


def create_assistant_error_message(
    content: str,
    *,
    api_error: str | None = None,
    error_details: str | None = None,
) -> AssistantMessage:
    """创建一个合成（synthetic）的助手错误消息。

    设置 ``is_api_error_message=True`` 和 ``model=SYNTHETIC_MODEL``，
    以便下游代码可以区分真实的模型输出与错误占位符。
    """
    msg = create_assistant_message(content)
    msg.model = SYNTHETIC_MODEL
    msg.is_api_error_message = True
    msg.api_error = api_error
    msg.error_details = error_details
    return msg


# ── 系统消息 ─────────────────────────────────────────────────────────────────


def create_system_message(
    content: str,
    level: str = "info",
) -> SystemMessage:
    """创建一个信息性的 SystemMessage。"""
    return SystemMessage(
        content=content,
        subtype=SystemMessageSubtype.INFORMATIONAL,
        level=level,
    )


def create_compact_boundary_message(
    trigger: str,
    pre_tokens: int,
    *,
    messages_summarized: int | None = None,
    user_context: str | None = None,
) -> SystemMessage:
    """创建一个压缩边界标记。"""
    return SystemMessage(
        content="",
        subtype=SystemMessageSubtype.COMPACT_BOUNDARY,
        compact_metadata=CompactMetadata(
            trigger=trigger,  # type: ignore[arg-type]
            pre_tokens=pre_tokens,
            user_context=user_context,
            messages_summarized=messages_summarized,
        ),
    )


def create_compact_clear_boundary_message(
    trigger: str,
    pre_tokens: int,
    tokens_saved: int,
    compacted_tool_ids: list[str],
    cleared_attachment_uuids: list[str],
) -> SystemMessage:
    """创建一个 Layer B（清除）边界标记。"""
    return SystemMessage(
        content="",
        subtype=SystemMessageSubtype.COMPACT_CLEAR_BOUNDARY,
        compact_clear_metadata=CompactClearMetadata(
            trigger=trigger,  # type: ignore[arg-type]
            pre_tokens=pre_tokens,
            tokens_saved=tokens_saved,
            compacted_tool_ids=compacted_tool_ids,
            cleared_attachment_uuids=cleared_attachment_uuids,
        ),
    )


# ── 附件消息 ─────────────────────────────────────────────────────


def create_attachment_message(
    attachment_type: str,
    *,
    content: str = "",
    metadata: dict[str, Any] | None = None,
) -> AttachmentMessage:
    """创建一个包裹 Attachment 的 AttachmentMessage。"""
    return AttachmentMessage(
        attachment=Attachment(
            type=attachment_type,
            content=content,
            metadata=metadata or {},
        ),
    )


# ── 图像辅助函数 ────────────────────────────────────────────────────────────


# 支持的图像 MIME 类型
_IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def create_image_block(file_path: str | Path) -> ImageBlock:
    """从文件路径创建一个 ImageBlock。

    读取图像文件，进行 base64 编码，并返回适合发送给多模态大模型的
    ImageBlock。

    Args:
        file_path: 图像文件的路径。

    Returns:
        包含 base64 编码图像数据的 ImageBlock。

    Raises:
        FileNotFoundError: 如果文件不存在。
        ValueError: 如果文件扩展名不受支持。
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")

    suffix = path.suffix.lower()
    if suffix not in _IMAGE_MIME_TYPES:
        raise ValueError(
            f"Unsupported image format: {suffix}. "
            f"Supported: {', '.join(_IMAGE_MIME_TYPES.keys())}"
        )

    mime_type = _IMAGE_MIME_TYPES[suffix]
    image_data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")

    return ImageBlock(
        source={
            "type": "base64",
            "media_type": mime_type,
            "data": image_data,
        }
    )


def create_user_message_with_image(
    text: str,
    image_path: str | Path,
    **kwargs,
) -> UserMessage:
    """创建一个包含文本和图像的用户消息。

    Args:
        text: 文本提示。
        image_path: 图像文件的路径。
        **kwargs: 传递给 create_user_message 的额外参数。
    """
    image_block = create_image_block(image_path)
    text_block = TextBlock(text=text)
    return create_user_message([text_block, image_block], **kwargs)
