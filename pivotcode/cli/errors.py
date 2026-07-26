"""CLI 错误分类和显示模块。"""

from __future__ import annotations


def classify_error(error: Exception) -> tuple[str, str | None]:
    """返回 (消息, 可选提示) 用于显示。

    消息始终包含原始错误文本。提示是基于模式匹配的可选建议——
    永远不会替代真正的错误。
    """
    msg = str(error)
    msg_lower = msg.lower()

    hint = None

    if "auth" in msg_lower or "api key" in msg_lower or "401" in msg:
        hint = "请检查你的 API 密钥和提供商设置。"

    elif "rate limit" in msg_lower or "429" in msg:
        hint = "请稍等片刻后重试。"

    elif "connection" in msg_lower or "timeout" in msg_lower or "network" in msg_lower:
        hint = "请检查你的网络连接。"

    elif "tool calling" in msg_lower or "function calling" in msg_lower:
        hint = (
            "对于不支持原生工具调用的模型，请使用 "
            "'/settings-project tool_call_format=hermes' 启用基于文本的工具调用。"
        )

    elif "context" in msg_lower and ("long" in msg_lower or "exceeded" in msg_lower):
        hint = "请尝试 /compact 或 /clear。"

    return f"错误：{msg}", hint
