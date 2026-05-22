"""Pivot Code 的消息类型。

这些 dataclass 表示流经系统的所有消息。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

# ── 内容块类型 ──────────────────────────────────────────────────────


@dataclass
class TextBlock:
    """一个文本内容块。"""
    text: str
    type: Literal["text"] = "text"


@dataclass
class ToolUseBlock:
    """来自模型的工具调用请求。"""
    id: str
    name: str
    input: dict[str, Any]
    type: Literal["tool_use"] = "tool_use"


@dataclass
class ToolResultBlock:
    """工具执行结果。"""
    tool_use_id: str
    content: str | list[TextBlock]
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"


@dataclass
class ThinkingBlock:
    """扩展思考内容（模型内部的推理过程）。"""
    thinking: str
    signature: str = ""
    type: Literal["thinking"] = "thinking"


@dataclass
class RedactedThinkingBlock:
    """被遮盖的思考内容。"""
    data: str
    type: Literal["redacted_thinking"] = "redacted_thinking"


@dataclass
class ImageBlock:
    """一个图像内容块。"""
    source: dict[str, Any]
    type: Literal["image"] = "image"


# 所有内容块类型的联合
ContentBlock = (
    TextBlock
    | ToolUseBlock
    | ToolResultBlock
    | ThinkingBlock
    | RedactedThinkingBlock
    | ImageBlock
)

# 可以出现在发送给 API 的用户消息中的内容
UserContentBlock = TextBlock | ToolResultBlock | ImageBlock

# 可以出现在来自 API 的助手消息中的内容
AssistantContentBlock = TextBlock | ToolUseBlock | ThinkingBlock | RedactedThinkingBlock


# ── 用量统计 ───────────────────────────────────────────────────────────


@dataclass
class Usage:
    """单次 API 响应的 token 用量。"""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_input(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def accumulate(self, other: Usage) -> None:
        """将另一个 Usage 的计数累加到自身（原地修改）。"""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens


# ── 消息来源 ───────────────────────────────────────────────────────────


@dataclass
class MessageOrigin:
    """消息的来源（provenance）。"""
    kind: str  # 'human'、'tool'、'system'、'compact'、'meta'
    source: str | None = None  # 例如工具名、hook 名


# ── 核心消息类型 ───────────────────────────────────────────────────────


@dataclass
class UserMessage:
    """一个 user 角色的消息（人类输入、工具结果，或系统注入的上下文）。"""
    content: str | list[UserContentBlock]
    uuid: UUID = field(default_factory=uuid4)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    # 元数据标志位
    hide_in_ui: bool = False  # 若为 True：不在聊天 UI 中显示，但会发送给 LLM
    hide_in_api: bool = False  # 若为 True：仅在 UI 中显示，不发送给 LLM
    is_compact_summary: bool = False  # 压缩输出

    # 工具结果关联
    tool_use_result: Any = None  # 结构化的工具输出
    source_tool_assistant_uuid: UUID | None = None  # 将 tool_result 关联到其 tool_use

    # 发送时的权限模式（用于回退/rewind）
    permission_mode: str | None = None
    origin: MessageOrigin | None = None

    # 压缩元数据（仅存在于压缩摘要消息上）
    summarize_metadata: dict[str, Any] | None = None

    type: Literal["user"] = "user"


@dataclass
class AssistantMessage:
    """一条 LLM 回复消息。"""
    content: list[AssistantContentBlock]
    uuid: UUID = field(default_factory=uuid4)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    # API 元数据
    model: str = ""
    stop_reason: str | None = None
    usage: Usage = field(default_factory=Usage)
    request_id: str | None = None

    # 错误追踪
    is_api_error_message: bool = False
    api_error: str | None = None  # 'invalid_request'、'max_output_tokens' 等
    error_details: str | None = None

    # 显示标志
    hide_in_api: bool = False  # 若为 True：仅在 UI 中显示，不发送给 LLM

    type: Literal["assistant"] = "assistant"

    @property
    def text(self) -> str:
        """提取拼接后的文本内容。"""
        parts = []
        for block in self.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
        return "".join(parts)

    @property
    def tool_use_blocks(self) -> list[ToolUseBlock]:
        """提取所有 tool_use 块。"""
        return [b for b in self.content if isinstance(b, ToolUseBlock)]

    @property
    def has_tool_use(self) -> bool:
        return any(isinstance(b, ToolUseBlock) for b in self.content)


# ── 系统消息子类型 ──────────────────────────────────────────────────


class SystemMessageSubtype(str, Enum):
    COMPACT_BOUNDARY = "compact_boundary"
    COMPACT_CLEAR_BOUNDARY = "compact_clear_boundary"
    API_ERROR = "api_error"
    LOCAL_COMMAND = "local_command"
    INFORMATIONAL = "informational"
    MEMORY_SAVED = "memory_saved"
    STOP_HOOK_SUMMARY = "stop_hook_summary"
    TURN_DURATION = "turn_duration"


@dataclass
class CompactMetadata:
    """压缩边界标记的元数据。"""
    trigger: Literal["manual", "auto"]
    pre_tokens: int
    user_context: str | None = None
    messages_summarized: int | None = None


@dataclass
class CompactClearMetadata:
    """Layer B（清除）边界标记的元数据。"""
    trigger: Literal["auto"]
    pre_tokens: int
    tokens_saved: int
    compacted_tool_ids: list[str] = field(default_factory=list)
    cleared_attachment_uuids: list[str] = field(default_factory=list)


@dataclass
class SystemMessage:
    """一个系统级消息（并非 API 的 system 提示词）。"""
    content: str
    subtype: SystemMessageSubtype
    uuid: UUID = field(default_factory=uuid4)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    level: str = "info"  # 'info'、'warning'、'error'
    hide_in_ui: bool = False

    # 子类型专属元数据
    compact_metadata: CompactMetadata | None = None
    compact_clear_metadata: CompactClearMetadata | None = None

    type: Literal["system"] = "system"


# ── 附件消息 ──────────────────────────────────────────────────────


@dataclass
class Attachment:
    """一个上下文附件（文件内容、搜索结果等）。"""
    type: str  # 'edited_text_file'、'hook_stopped_continuation'、'max_iterations_per_turn_reached' 等
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AttachmentMessage:
    """在两个回合之间注入的附件。"""
    attachment: Attachment
    uuid: UUID = field(default_factory=uuid4)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    type: Literal["attachment"] = "attachment"


@dataclass
class ProgressMessage:
    """来自工具执行的实时进度更新。"""
    tool_use_id: str
    data: dict[str, Any]
    parent_tool_use_id: str = ""
    uuid: UUID = field(default_factory=uuid4)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    type: Literal["progress"] = "progress"


# ── 流式事件 ────────────────────────────────────────────────────────────


@dataclass
class RequestStartEvent:
    """标记一次新 API 请求的开始。"""
    type: Literal["stream_request_start"] = "stream_request_start"


# ── 联合类型 ───────────────────────────────────────────────────────────────


Message = (
    UserMessage
    | AssistantMessage
    | SystemMessage
    | AttachmentMessage
    | ProgressMessage
)

StreamEvent = (
    RequestStartEvent
    | AssistantMessage
    | ProgressMessage
)

# 查询循环生成器能够产出的所有内容
QueryYield = Message | StreamEvent | RequestStartEvent


# ── 辅助函数 ──────────────────────────────────────────────────────────────────


def is_compact_boundary(message: Message) -> bool:
    """检查某条消息是否为压缩边界标记。"""
    return (
        isinstance(message, SystemMessage)
        and message.subtype == SystemMessageSubtype.COMPACT_BOUNDARY
    )


def get_messages_after_compact_boundary(messages: list[Message]) -> list[Message]:
    """返回从最后一个压缩边界之后的消息。
    如果不存在边界，则返回全部消息。
    """
    for i in range(len(messages) - 1, -1, -1):
        if is_compact_boundary(messages[i]):
            return messages[i:]
    return messages


def get_last_assistant_message(messages: list[Message]) -> AssistantMessage | None:
    """在列表中查找最后一条助手消息。"""
    for msg in reversed(messages):
        if isinstance(msg, AssistantMessage):
            return msg
    return None
