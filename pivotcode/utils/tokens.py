"""Token 计数与上下文窗口工具。

我们使用两个信号来做 token 核算，按以下优先顺序：

1. **提供方报告的 ``usage``**——API 上次调用返回的精确 token 计数。
   直接用于显示（``/status`` 的一行摘要）。
2. **调用前估算**——在压缩管线中、API 调用之前需要用到，
   因为此时还无法询问提供方。在 LiteLLM 可用时委托给
   ``litellm.token_counter``（真实的、针对具体模型的
   分词器），否则使用 chars/3 的启发式方法。

为了避免在压缩预检中预算给小了，我们取以下两者的 ``max``：

- ``usage_based``  = 上次调用的输入 + 输出 + 此后再加入的 token
- ``full_estimate`` = 对整个调用前 payload 的直接计数

此处曾经有一个 ``TokenEstimator`` / EMA 校准比例，现已移除——
校准在数值上是错误的（详见提交说明）。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── 公开别名 ───────────────────────────────────────────────────────────

MODEL_CONTEXT_WINDOW_DEFAULT = 200_000
MAX_OUTPUT_TOKENS_DEFAULT = 32_000

# 仅在无分词器可用时使用的回退比例。
# 3 字符/token 对大多数模型（英文文本 + 代码）而言是偏保守的估计。
CHARS_PER_TOKEN_FALLBACK = 3.0


# ── 原始计数原语 ──────────────────────────────────────────────────────────


def _chars_to_tokens(chars: int) -> int:
    """通过扁平回退比例将字符转换为 token。"""
    return max(1, int(chars / CHARS_PER_TOKEN_FALLBACK))


def rough_token_count(text: str) -> int:
    """通过 chars/3 回退方法估算字符串中的 token 数。"""
    return _chars_to_tokens(len(text))


def _content_block_tokens(block: Any) -> int:
    """估算单个内容块的 token 数（回退启发式）。"""
    if isinstance(block, str):
        return rough_token_count(block)
    if hasattr(block, "text"):
        return rough_token_count(block.text)
    if hasattr(block, "thinking"):
        return rough_token_count(block.thinking)
    if hasattr(block, "content"):
        inner = block.content
        if isinstance(inner, str):
            return rough_token_count(inner)
        if isinstance(inner, list):
            return sum(_content_block_tokens(b) for b in inner)
    if hasattr(block, "input") and isinstance(block.input, dict):
        name_tokens = rough_token_count(getattr(block, "name", ""))
        input_tokens = rough_token_count(str(block.input))
        return name_tokens + input_tokens
    if hasattr(block, "summary"):
        return rough_token_count(block.summary)
    if hasattr(block, "data"):
        return rough_token_count(str(block.data))
    return 4


def estimate_message_tokens(messages: list) -> int:
    """使用 chars/3 启发式估算消息列表的 token 数。

    如需理解模型分词器、更精确的计数，请使用
    :func:`count_tokens_for_call`。
    """
    total = 0
    for msg in messages:
        total += 4  # 每条消息的固定开销
        content = getattr(msg, "content", None)
        if content is None:
            if hasattr(msg, "attachment"):
                att = msg.attachment
                total += rough_token_count(getattr(att, "content", ""))
                total += rough_token_count(getattr(att, "type", ""))
            elif hasattr(msg, "summary"):
                total += rough_token_count(msg.summary)
            elif hasattr(msg, "data") and isinstance(msg.data, dict):
                total += rough_token_count(str(msg.data))
            continue
        if isinstance(content, str):
            total += rough_token_count(content)
        elif isinstance(content, list):
            total += sum(_content_block_tokens(b) for b in content)
    return total


def count_message_chars(messages: list) -> int:
    """统计消息列表中的总字符数。"""
    total = 0
    for msg in messages:
        content = getattr(msg, "content", None)
        if content is None:
            if hasattr(msg, "attachment"):
                total += len(getattr(msg.attachment, "content", ""))
            elif hasattr(msg, "summary"):
                total += len(msg.summary)
            continue
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            total += sum(_count_block_chars(b) for b in content)
    return total


def _count_block_chars(block: Any) -> int:
    if isinstance(block, str):
        return len(block)
    if hasattr(block, "text"):
        return len(block.text)
    if hasattr(block, "thinking"):
        return len(block.thinking)
    if hasattr(block, "content"):
        inner = block.content
        if isinstance(inner, str):
            return len(inner)
        if isinstance(inner, list):
            return sum(_count_block_chars(b) for b in inner)
    if hasattr(block, "input") and isinstance(block.input, dict):
        return len(getattr(block, "name", "")) + len(str(block.input))
    return 0


# ── 基于 LiteLLM 的预调用估算计数 ──────────────────────────────────


def _messages_for_litellm(messages: list) -> list[dict]:
    """将我们的 Message 对象序列化为 ``litellm.token_counter`` 所期望的
    简单字典形态（``role`` + ``content`` 字符串）。

    该函数具有一定的容错性——任何无法干净序列化的内容都会被跳过，
    从而保证我们总能产出 *某个* 估算值。我们并不追求逐字节的精确复现；
    调用方本来也会将其与基于 usage 的计数取 ``max()``。
    """
    out: list[dict] = []
    for msg in messages:
        role = getattr(msg, "role", None)
        if role is None:
            # 从类名推断角色。
            cls = type(msg).__name__
            role = (
                "user" if "User" in cls
                else "assistant" if "Assistant" in cls
                else "system" if "System" in cls
                else "user"
            )
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            # 将结构化内容扁平化为单个文本字符串。
            parts: list[str] = []
            for b in content:
                if hasattr(b, "text") and b.text:
                    parts.append(b.text)
                elif hasattr(b, "thinking") and b.thinking:
                    parts.append(b.thinking)
                elif hasattr(b, "input") and isinstance(b.input, dict):
                    parts.append(str(b.input))
                elif hasattr(b, "content"):
                    inner = b.content
                    if isinstance(inner, str):
                        parts.append(inner)
                    elif isinstance(inner, list):
                        for ib in inner:
                            if hasattr(ib, "text") and ib.text:
                                parts.append(ib.text)
            content = "\n".join(p for p in parts if p)
        elif not isinstance(content, str):
            content = str(content)
        out.append({"role": role, "content": content})
    return out


def count_tokens_for_call(
    model: str | None,
    messages: list,
    *,
    system: str | list[str] | None = None,
    tools: list | None = None,
) -> int:
    """估算一次预期 API 调用的 token 数。

    当 LiteLLM 可导入时，使用 ``litellm.token_counter``（针对大多数主流
    和本地模型的、真实的、与具体模型相关的分词器）。当它不可用，或模型
    无法识别时，回退到 chars/3 启发式方法。
    """
    # 构建 prompt 的形态。
    msg_dicts = _messages_for_litellm(messages)

    if system:
        if isinstance(system, list):
            system_str = "\n\n".join(system)
        else:
            system_str = system
        if system_str:
            msg_dicts = [{"role": "system", "content": system_str}] + msg_dicts

    try:
        import litellm  # type: ignore
    except Exception:
        litellm = None  # type: ignore

    if litellm is not None and model:
        try:
            kwargs: dict[str, Any] = {"model": model, "messages": msg_dicts}
            if tools:
                kwargs["tools"] = tools
            return int(litellm.token_counter(**kwargs))
        except Exception as exc:
            logger.debug("litellm.token_counter failed (%s); using fallback", exc)

    # 回退：对 messages + system + 以字符串表示的 tools 使用 chars/3。
    total = estimate_message_tokens(messages)
    if system:
        system_str = "\n\n".join(system) if isinstance(system, list) else system
        total += rough_token_count(system_str)
    if tools:
        # tools 可能是 schema 字典或 Tool 对象——以防御性方式字符串化。
        total += rough_token_count(str(tools))
    return total


def predicted_next_call_tokens(
    model: str | None,
    messages: list,
    *,
    system: str | list[str] | None = None,
    tools: list | None = None,
    last_input_tokens: int = 0,
    last_output_tokens: int = 0,
    new_messages_since_last_call: list | None = None,
) -> int:
    """估算即将到来的 API 调用的 token 数。

    返回 ``max(usage_based, full_estimate)``，其中：

    - ``usage_based`` = ``last_input_tokens + last_output_tokens + 自上次调用以来
      新增消息的 token 数``。当提供方填充了 ``usage`` 时，这接近精确值。
    - ``full_estimate`` = ``count_tokens_for_call(messages, ...)`` —— 对整个
      即将到来的 payload 的、基于分词器的估算。

    取最大值可防止预算给小了：如果任一侧出错，另一侧会保守地将其封顶。
    当提供方没有填充 ``usage``（即 ``last_input_tokens == 0``）时，
    我们直接落到 ``full_estimate``。
    """
    full_estimate = count_tokens_for_call(
        model, messages, system=system, tools=tools,
    )

    if last_input_tokens > 0:
        added = 0
        if new_messages_since_last_call:
            added = count_tokens_for_call(model, new_messages_since_last_call)
        usage_based = last_input_tokens + last_output_tokens + added
        return max(usage_based, full_estimate)

    return full_estimate


# ── 阈值工具 ──────────────────────────────────────────────────────────────


def _s(settings: dict | None, key: str, default: Any) -> Any:
    """从设置字典中读取，若缺失则回退到内置默认值。"""
    if settings is not None and key in settings and settings[key] is not None:
        return settings[key]
    return default


def get_auto_compact_threshold(
    context_window: int,
    max_output_tokens: int | None = None,
    settings: dict | None = None,
) -> int:
    """计算触发自动压缩的 token 数阈值。"""
    compact_max = _s(settings, "compact_max_output_tokens", 20_000)
    mot = max_output_tokens or compact_max
    effective = context_window - max(mot, compact_max)
    return effective - _s(settings, "auto_compact_buffer_tokens", 13_000)


def calculate_token_warning_state(
    token_usage: int,
    context_window: int,
    max_output_tokens: int | None = None,
    settings: dict | None = None,
) -> dict:
    """计算上下文窗口使用警告状态。"""
    compact_max = _s(settings, "compact_max_output_tokens", 20_000)
    mot = max_output_tokens or compact_max
    usable = context_window - max(mot, compact_max)
    remaining = usable - token_usage
    percent_left = max(0.0, remaining / usable) if usable > 0 else 0.0

    return {
        "percent_left": percent_left,
        "is_above_warning": remaining < _s(settings, "warning_threshold_buffer_tokens", 20_000),
        "is_above_error": remaining < _s(settings, "auto_compact_buffer_tokens", 13_000),
        "is_at_blocking_limit": remaining < _s(settings, "blocking_limit_buffer_tokens", 3_000),
    }
