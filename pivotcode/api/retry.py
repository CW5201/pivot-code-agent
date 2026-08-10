"""针对 LLM 提供方调用的指数退避重试逻辑。"""

import asyncio
import logging
import random
from collections.abc import AsyncGenerator

from pivotcode.api.errors import (
    OverloadedError,
    PromptTooLongError,
    RateLimitError,
    classify_error,
    is_retryable_error,
)
from pivotcode.providers.base import (
    LLMProvider,
    ProviderStreamEvent,
    StreamError,
    ThinkingConfig,
    ToolSchema,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3
BASE_DELAY = 1.0  # 秒
MAX_DELAY = 60.0  # 秒


def _compute_delay(attempt: int, retry_after: float | None = None) -> float:
    """计算指定重试次数的退避延迟。

    使用带全抖动的指数退避：
        delay = min(BASE_DELAY * 2^attempt + random(0, 1), MAX_DELAY)

    如果服务端提供了 Retry-After 提示，则取计算出的延迟与该提示两者中的较大值。
    """
    exp_delay = BASE_DELAY * (2 ** attempt) + random.random()
    delay = min(exp_delay, MAX_DELAY)
    if retry_after is not None and retry_after > delay:
        delay = min(retry_after, MAX_DELAY)
    return delay


def _extract_retry_after(error: Exception) -> float | None:
    """如果 RateLimitError 中存在，则提取 retry-after 提示。"""
    if isinstance(error, RateLimitError):
        return error.retry_after
    return None


def _stream_error_to_exception(event: StreamError) -> Exception:
    """将一个 StreamError 事件转换为带类型的异常。"""
    msg = event.error
    etype = event.error_type
    status = event.status_code

    if etype == "overloaded" or status == 529:
        return OverloadedError(msg)
    if status == 429 or "rate limit" in msg.lower() or "too many requests" in msg.lower():
        return RateLimitError(msg)
    if "prompt" in msg.lower() and ("too long" in msg.lower() or "context" in msg.lower()):
        return PromptTooLongError(msg)
    return RuntimeError(msg)


async def stream_with_retry(
    provider: LLMProvider,
    messages: list[dict],
    system: list[str],
    tools: list[ToolSchema],
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    thinking: ThinkingConfig | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    fallback_provider: LLMProvider | None = None,
    **kwargs,
) -> AsyncGenerator[ProviderStreamEvent, None]:
    """在发生临时错误时自动重试地进行流式输出。

    实现带抖动的指数退避。当所有重试都用尽仍持续失败时，可选地回退到
    ``fallback_provider``。

    不可重试的错误（例如提示词过长、请求无效）会立即抛出，不消耗重试额度。

    Yields:
        来自底层提供方的 ProviderStreamEvent 实例。

    Raises:
        所有重试（以及可选的回退）用尽后最后遇到的异常。
    """
    # 使用具体异常初始化，以保证此处永远不会遇到 `None`。
    # 发生任何可重试失败时，会被真实错误覆盖。
    last_error: Exception = RuntimeError(
        "API call failed with no error detail recorded"
    )

    for attempt in range(max_retries + 1):
        try:
            stream = provider.stream(
                messages,
                system,
                tools,
                model=model,
                max_tokens=max_tokens,
                thinking=thinking,
                **kwargs,
            )
            # 缓冲事件，以便在因可重试失败而输出部分内容之前，能够检测到
            # 流中途出现的错误。但为了效率，我们选择即时输出，并且只在
            # 任何内容事件*之前*到达的错误上进行重试。
            events_yielded = 0
            async for event in stream:
                # 检测来自提供方的 StreamError 事件
                if isinstance(event, StreamError):
                    exc = _stream_error_to_exception(event)
                    if not is_retryable_error(exc):
                        raise exc
                    if events_yielded > 0:
                        # 已经输出了内容；无法透明地重试。
                        # 重新抛出，交由调用方处理。
                        raise exc
                    # 在任意内容之前出现可重试错误 —— 跳出以进入重试循环
                    last_error = exc
                    break
                else:
                    yield event
                    events_yielded += 1
            else:
                # 流正常完成（未 break）
                return

            # 如果是因可重试的 StreamError 而跳出，则继续执行下面的重试逻辑。

        except Exception as exc:
            last_error = exc
            category = classify_error(exc)

            if not is_retryable_error(exc):
                logger.error(
                    "Non-retryable error (category=%s): %s", category, exc
                )
                raise

        # 出现了可重试错误。记录日志并退避。
        if attempt < max_retries:
            retry_after = _extract_retry_after(last_error)
            delay = _compute_delay(attempt, retry_after)
            logger.warning(
                "Retryable error on attempt %d/%d (category=%s): %s  "
                "Retrying in %.1fs...",
                attempt + 1,
                max_retries + 1,
                classify_error(last_error),
                last_error,
                delay,
            )
            await asyncio.sleep(delay)
        # else: 将退出循环

    # 所有重试已用尽。如果可用则尝试回退提供方。
    if fallback_provider is not None:
        logger.warning(
            "All %d retries exhausted. Falling back to fallback provider.",
            max_retries + 1,
        )
        try:
            stream = fallback_provider.stream(
                messages,
                system,
                tools,
                model=model,
                max_tokens=max_tokens,
                thinking=thinking,
                **kwargs,
            )
            async for event in stream:
                if isinstance(event, StreamError):
                    raise _stream_error_to_exception(event)
                yield event
            return
        except Exception as fallback_exc:
            logger.error("Fallback provider also failed: %s", fallback_exc)
            # 抛出原始错误，并以回退错误作为链式原因
            raise last_error from fallback_exc

    # 未配置回退或回退不可用 —— 抛出最后一个错误
    logger.error(
        "All %d retries exhausted. Raising last error: %s",
        max_retries + 1,
        last_error,
    )
    raise last_error
