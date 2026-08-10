"""事件序列化 —— 将智能体事件转换为 OutputEvent。

这是将内部消息/事件类型转换为
可 JSON 序列化的 :class:`OutputEvent` 实例的唯一位置。CLI 与 GUI 两种
前端都接收相同的格式。

复用 ``transcript.message_to_dict()`` 进行内容块序列化，
以避免重复实现块到字典的逻辑。
"""

from __future__ import annotations

from pivotcode.gui.protocol import OutputEvent
from pivotcode.messages.types import (
    AssistantMessage,
    AttachmentMessage,
    Message,
    ProgressMessage,
    RequestStartEvent,
    StreamEvent,
    SystemMessage,
    UserMessage,
)
from pivotcode.session.transcript import message_to_dict


def agent_event_to_output(event: StreamEvent | Message) -> OutputEvent:
    """将一个智能体产出的事件转换为可序列化的 OutputEvent。

    处理 ``query_events_async()`` 与 ``query_loop()``
    产生的所有事件类型。
    """
    if isinstance(event, RequestStartEvent):
        return OutputEvent(type="request_start", data={}, original=event)

    if isinstance(event, AssistantMessage):
        data = message_to_dict(event)
        etype = "assistant_delta" if event.hide_in_api else "assistant_message"
        return OutputEvent(type=etype, data=data, original=event)

    if isinstance(event, UserMessage):
        data = message_to_dict(event)
        return OutputEvent(type="user_message", data=data, original=event)

    if isinstance(event, SystemMessage):
        data = message_to_dict(event)
        return OutputEvent(type="system_message", data=data, original=event)

    if isinstance(event, AttachmentMessage):
        data = message_to_dict(event)
        return OutputEvent(type="attachment_message", data=data, original=event)

    if isinstance(event, ProgressMessage):
        data = message_to_dict(event)
        return OutputEvent(type="progress_message", data=data, original=event)

    # 未知类型的兜底处理
    return OutputEvent(type="unknown", data={"repr": repr(event)}, original=event)


def cost_summary_event(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    cost_usd: float,
    cost_unknown: bool,
) -> OutputEvent:
    """创建一个成本概要 OutputEvent（每轮对话后发出）。"""
    return OutputEvent(
        type="cost_summary",
        data={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cost_usd": cost_usd,
            "cost_unknown": cost_unknown,
        },
    )


def local_output_event(text: str, style: str = "default") -> OutputEvent:
    """为斜杠命令的结果创建一个本地输出事件。

    用于应在 CLI 与 GUI 中同时出现的非智能体输出
    （帮助表格、状态面板、diff 输出等）。
    """
    return OutputEvent(
        type="local_output",
        data={"text": text, "style": style},
    )
