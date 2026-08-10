"""API 错误分类与检测。"""


class PromptTooLongError(Exception):
    """提示词超出了模型的上下文窗口。"""

    def __init__(self, message: str, token_gap: int | None = None):
        super().__init__(message)
        self.token_gap = token_gap


class MaxOutputTokensError(Exception):
    """响应达到了 max_output_tokens 上限。"""


class RateLimitError(Exception):
    """超出了速率限制。"""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class OverloadedError(Exception):
    """API 过载（529）。"""


# ── 检测辅助函数 ──────────────────────────────────────────────────────


_PROMPT_TOO_LONG_PATTERNS = (
    "prompt is too long",
    "prompt too long",
    "context window",
    "context length",
    "maximum context",
    "token limit",
    "exceeds the model",
)


def is_prompt_too_long(error_message: str) -> bool:
    """如果错误消息表示提示词过长的失败，则返回 True。"""
    lower = error_message.lower()
    return any(pattern in lower for pattern in _PROMPT_TOO_LONG_PATTERNS)


_RETRYABLE_TYPES = (RateLimitError, OverloadedError, ConnectionError, TimeoutError)


def is_retryable_error(error: Exception) -> bool:
    """如果错误是临时性的、值得重试，则返回 True。

    可重试的错误包括速率限制、服务器过载、连接错误和超时。提示词过长
    与 max-output-tokens 错误*不可*重试，因为重新发送相同的请求会得到
    相同的失败结果。
    """
    if isinstance(error, _RETRYABLE_TYPES):
        return True
    # 某些提供商会把临时性失败包装在通用的异常中。
    msg = str(error).lower()
    if any(kw in msg for kw in ("rate limit", "429", "529", "overloaded", "too many requests")):
        return True
    if any(kw in msg for kw in ("connection", "timeout", "timed out", "reset by peer")):
        return True
    return False


def classify_error(error: Exception) -> str:
    """将一个异常分类为可读的类别字符串。

    类别：
        'prompt_too_long'   - 超出上下文窗口
        'max_output_tokens' - 达到输出长度上限
        'rate_limit'        - 429 / 速率限制
        'overloaded'        - 529 / 服务器过载
        'connection'        - 网络层失败
        'timeout'           - 请求超时
        'unknown'           - 其他所有情况
    """
    if isinstance(error, PromptTooLongError):
        return "prompt_too_long"
    if isinstance(error, MaxOutputTokensError):
        return "max_output_tokens"
    if isinstance(error, RateLimitError):
        return "rate_limit"
    if isinstance(error, OverloadedError):
        return "overloaded"
    if isinstance(error, ConnectionError):
        return "connection"
    if isinstance(error, TimeoutError):
        return "timeout"

    # 基于消息文本启发式的兜底分类
    msg = str(error).lower()
    if is_prompt_too_long(msg):
        return "prompt_too_long"
    if "rate limit" in msg or "429" in msg or "too many requests" in msg:
        return "rate_limit"
    if "overloaded" in msg or "529" in msg:
        return "overloaded"
    if any(kw in msg for kw in ("connection", "reset by peer")):
        return "connection"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"

    return "unknown"
