"""用于会话持久化的转录记录。

每个会话都存储为 ``.pivot/sessions/<session_id>/transcript.jsonl`` 下的一个
JSONL 文件。每行一个 JSON 对象，每条消息一行。这样可以实现高效的
仅追加写入与流式读取。
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pivotcode.messages.types import (
    AssistantMessage,
    Attachment,
    AttachmentMessage,
    CompactClearMetadata,
    CompactMetadata,
    ContentBlock,
    ImageBlock,
    Message,
    RedactedThinkingBlock,
    SystemMessage,
    SystemMessageSubtype,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    UserMessage,
)
from pivotcode.session.session import get_session_dir

logger = logging.getLogger(__name__)


# ── 路径 ──────────────────────────────────────────────────────────────────


def get_session_transcript_path(cwd: str, session_id: str) -> Path:
    """返回给定会话的 transcript JSONL 文件路径。

    新布局：``.pivot/sessions/<session_id>/transcript.jsonl``
    """
    return get_session_dir(cwd, session_id) / "transcript.jsonl"



# ── 写入 / 读取 ───────────────────────────────────────────────────────────


async def record_transcript(
    session_id: str,
    messages: list[Message],
    *,
    cwd: str | None = None,
) -> None:
    """将 *messages* 写入会话 transcript（JSONL 格式）。

    会覆盖同一会话之前的 transcript。第一行是一个元数据对象，包含会话的
    *cwd*、会话 ID 与创建时间戳，以便 ``get_last_session_id`` 能按工作目录
    进行过滤。

    当提供 *cwd* 时，使用新的按项目划分的会话布局
    （``.pivot/sessions/<id>/transcript.jsonl``）。
    """
    if not cwd:
        raise ValueError("cwd is required for record_transcript")
    path = get_session_transcript_path(cwd, session_id)

    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from pivotcode.utils.atomic_io import atomic_write_text
        metadata = {
            "_metadata": {
                "cwd": cwd or "",
                "session_id": session_id,
                "created_at": datetime.now(UTC).isoformat(),
            }
        }
        lines = [json.dumps(metadata, default=str)]
        lines.extend(
            json.dumps(message_to_dict(msg), default=str) for msg in messages
        )
        atomic_write_text(path, "\n".join(lines) + "\n")
    except OSError as exc:
        logger.warning("Failed to write transcript %s: %s", path, exc)


async def load_transcript(session_id: str, *, cwd: str | None = None) -> list[Message] | None:
    """从会话 transcript 加载消息。

    如果文件不存在或无法读取则返回 ``None``。第一行可能是一个元数据对象
    （``_metadata`` 键），在重建消息时会被跳过。单个格式错误的行会以
    警告形式跳过。
    """
    if not cwd:
        raise ValueError("cwd is required for load_transcript")
    path = get_session_transcript_path(cwd, session_id)

    if not path.is_file():
        return None

    messages: list[Message] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    # 跳过元数据行
                    if "_metadata" in d:
                        continue
                    messages.append(dict_to_message(d))
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    logger.warning(
                        "Skipping malformed line %d in %s: %s", lineno, path, exc
                    )
    except OSError as exc:
        logger.warning("Failed to read transcript %s: %s", path, exc)
        return None

    return messages if messages else None


# ── 序列化辅助函数 ──────────────────────────────────────────────────────────


def _uuid_to_str(val: UUID | None) -> str | None:
    """将 UUID 转换为其字符串表示，或对 None 原样透传。"""
    if val is None:
        return None
    return str(val)


def _content_block_to_dict(block: ContentBlock) -> dict:
    """将单个内容块序列化为字典。"""
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
            content = [_content_block_to_dict(b) for b in content]
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": content,
            "is_error": block.is_error,
        }
    if isinstance(block, ThinkingBlock):
        return {
            "type": "thinking",
            "thinking": block.thinking,
            "signature": block.signature,
        }
    if isinstance(block, RedactedThinkingBlock):
        return {"type": "redacted_thinking", "data": block.data}
    if isinstance(block, ImageBlock):
        return {"type": "image", "source": block.source}
    # 兜底：尝试使用 __dict__
    return {"type": getattr(block, "type", "unknown"), **getattr(block, "__dict__", {})}


def _dict_to_content_block(d: dict) -> ContentBlock:
    """将字典反序列化为合适的内容块类型。"""
    block_type = d.get("type", "")
    if block_type == "text":
        return TextBlock(text=d["text"])
    if block_type == "tool_use":
        return ToolUseBlock(id=d["id"], name=d["name"], input=d.get("input", {}))
    if block_type == "tool_result":
        content = d.get("content", "")
        if isinstance(content, list):
            content = [_dict_to_content_block(b) for b in content]
        return ToolResultBlock(
            tool_use_id=d["tool_use_id"],
            content=content,
            is_error=d.get("is_error", False),
        )
    if block_type == "thinking":
        return ThinkingBlock(
            thinking=d.get("thinking", ""), signature=d.get("signature", "")
        )
    if block_type == "redacted_thinking":
        return RedactedThinkingBlock(data=d.get("data", ""))
    if block_type == "image":
        return ImageBlock(source=d.get("source", {}))
    # 兜底
    return TextBlock(text=str(d))


def message_to_dict(msg: Message) -> dict:
    """将消息序列化为 JSON 兼容的字典。"""
    if isinstance(msg, UserMessage):
        content = msg.content
        if isinstance(content, list):
            content = [_content_block_to_dict(b) for b in content]
        out = {
            "type": "user",
            "content": content,
            "uuid": str(msg.uuid),
            "timestamp": msg.timestamp,
            "hide_in_ui": msg.hide_in_ui,
            "hide_in_api": msg.hide_in_api,
            "is_compact_summary": msg.is_compact_summary,
            "permission_mode": msg.permission_mode,
        }
        # 保留 tool_use → tool_result 的关联，以便配对在恢复后仍然有效。
        if msg.source_tool_assistant_uuid is not None:
            out["source_tool_assistant_uuid"] = str(msg.source_tool_assistant_uuid)
        if msg.origin is not None:
            out["origin"] = {
                "kind": msg.origin.kind,
                "source": msg.origin.source,
            }
        return out

    if isinstance(msg, AssistantMessage):
        return {
            "type": "assistant",
            "content": [_content_block_to_dict(b) for b in msg.content],
            "uuid": str(msg.uuid),
            "timestamp": msg.timestamp,
            "model": msg.model,
            "stop_reason": msg.stop_reason,
            "usage": {
                "input_tokens": msg.usage.input_tokens,
                "output_tokens": msg.usage.output_tokens,
                "cache_creation_input_tokens": msg.usage.cache_creation_input_tokens,
                "cache_read_input_tokens": msg.usage.cache_read_input_tokens,
            },
            "is_api_error_message": msg.is_api_error_message,
            "api_error": msg.api_error,
            "error_details": msg.error_details,
            "hide_in_api": msg.hide_in_api,
        }

    if isinstance(msg, SystemMessage):
        d: dict = {
            "type": "system",
            "content": msg.content,
            "subtype": msg.subtype.value,
            "uuid": str(msg.uuid),
            "timestamp": msg.timestamp,
            "level": msg.level,
            "hide_in_ui": msg.hide_in_ui,
        }
        if msg.compact_metadata is not None:
            d["compact_metadata"] = {
                "trigger": msg.compact_metadata.trigger,
                "pre_tokens": msg.compact_metadata.pre_tokens,
                "user_context": msg.compact_metadata.user_context,
                "messages_summarized": msg.compact_metadata.messages_summarized,
            }
        if msg.compact_clear_metadata is not None:
            d["compact_clear_metadata"] = {
                "trigger": msg.compact_clear_metadata.trigger,
                "pre_tokens": msg.compact_clear_metadata.pre_tokens,
                "tokens_saved": msg.compact_clear_metadata.tokens_saved,
                "compacted_tool_ids": msg.compact_clear_metadata.compacted_tool_ids,
                "cleared_attachment_uuids": msg.compact_clear_metadata.cleared_attachment_uuids,
            }
        return d

    if isinstance(msg, AttachmentMessage):
        return {
            "type": "attachment",
            "uuid": str(msg.uuid),
            "timestamp": msg.timestamp,
            "attachment": {
                "type": msg.attachment.type,
                "content": msg.attachment.content,
                "metadata": msg.attachment.metadata,
            },
        }

    # ProgressMessage 或未知类型 —— 尽力处理
    return {"type": getattr(msg, "type", "unknown"), **getattr(msg, "__dict__", {})}


def dict_to_message(d: dict) -> Message:
    """从字典反序列化一条消息。

    根据 ``type`` 字段重建正确的 Message 子类型。
    """
    msg_type = d.get("type", "")

    if msg_type == "user":
        content = d.get("content", "")
        if isinstance(content, list):
            content = [_dict_to_content_block(b) for b in content]
        src_uuid_str = d.get("source_tool_assistant_uuid")
        src_uuid = UUID(src_uuid_str) if src_uuid_str else None
        origin_d = d.get("origin")
        origin_obj = None
        if isinstance(origin_d, dict) and "kind" in origin_d:
            from pivotcode.messages.types import MessageOrigin
            origin_obj = MessageOrigin(
                kind=origin_d["kind"], source=origin_d.get("source"),
            )
        return UserMessage(
            content=content,
            uuid=UUID(d["uuid"]) if "uuid" in d else None,
            timestamp=d.get("timestamp", ""),
            hide_in_ui=d.get("hide_in_ui", False),
            hide_in_api=d.get("hide_in_api", False),
            is_compact_summary=d.get("is_compact_summary", False),
            permission_mode=d.get("permission_mode"),
            source_tool_assistant_uuid=src_uuid,
            origin=origin_obj,
        )

    if msg_type == "assistant":
        content_blocks = [
            _dict_to_content_block(b) for b in d.get("content", [])
        ]
        usage_d = d.get("usage", {})
        return AssistantMessage(
            content=content_blocks,
            uuid=UUID(d["uuid"]) if "uuid" in d else None,
            timestamp=d.get("timestamp", ""),
            model=d.get("model", ""),
            stop_reason=d.get("stop_reason"),
            usage=Usage(
                input_tokens=usage_d.get("input_tokens", 0),
                output_tokens=usage_d.get("output_tokens", 0),
                cache_creation_input_tokens=usage_d.get("cache_creation_input_tokens", 0),
                cache_read_input_tokens=usage_d.get("cache_read_input_tokens", 0),
            ),
            is_api_error_message=d.get("is_api_error_message", False),
            api_error=d.get("api_error"),
            error_details=d.get("error_details"),
            hide_in_api=d.get("hide_in_api", False),
        )

    if msg_type == "system":
        compact_meta = None
        if "compact_metadata" in d and d["compact_metadata"] is not None:
            cm = d["compact_metadata"]
            compact_meta = CompactMetadata(
                trigger=cm["trigger"],
                pre_tokens=cm["pre_tokens"],
                user_context=cm.get("user_context"),
                messages_summarized=cm.get("messages_summarized"),
            )
        compact_clear_meta = None
        if "compact_clear_metadata" in d and d["compact_clear_metadata"] is not None:
            mm = d["compact_clear_metadata"]
            compact_clear_meta = CompactClearMetadata(
                trigger=mm["trigger"],
                pre_tokens=mm["pre_tokens"],
                tokens_saved=mm["tokens_saved"],
                compacted_tool_ids=mm.get("compacted_tool_ids", []),
                cleared_attachment_uuids=mm.get("cleared_attachment_uuids", []),
            )
        return SystemMessage(
            content=d.get("content", ""),
            subtype=SystemMessageSubtype(d["subtype"]),
            uuid=UUID(d["uuid"]) if "uuid" in d else None,
            timestamp=d.get("timestamp", ""),
            level=d.get("level", "info"),
            hide_in_ui=d.get("hide_in_ui", False),
            compact_metadata=compact_meta,
            compact_clear_metadata=compact_clear_meta,
        )

    if msg_type == "attachment":
        att_d = d.get("attachment", {})
        return AttachmentMessage(
            attachment=Attachment(
                type=att_d.get("type", ""),
                content=att_d.get("content", ""),
                metadata=att_d.get("metadata", {}),
            ),
            uuid=UUID(d["uuid"]) if "uuid" in d else None,
            timestamp=d.get("timestamp", ""),
        )

    # 兜底：作为带有原始内容的 UserMessage 返回
    logger.warning("Unknown message type %r, falling back to UserMessage", msg_type)
    return UserMessage(content=str(d))
