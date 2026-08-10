"""按会话跟踪 API 成本。

CostTracker 负责定价逻辑（某个模型的费用是多少？），并将累计总额的存储
委托给 SessionState（与磁盘关联）。各模型的用量和 API 耗时仅保存在内存中
（仅用于展示，不持久化）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pivotcode.messages.types import Usage

if TYPE_CHECKING:
    from pivotcode.session.state import SessionState

# Anthropic 模型每百万 token 的定价（硬编码）。
# 来源：Anthropic 定价页 + litellm 注册表交叉核对。
# 这些由 AnthropicProvider 使用。LiteLLMProvider 使用 litellm 的注册表。
ANTHROPIC_PRICING: dict[str, dict[str, float]] = {
    # 当前代
    "claude-sonnet-4-6": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
    "claude-opus-4-6": {
        "input": 5.0,
        "output": 25.0,
        "cache_read": 0.50,
        "cache_write": 6.25,
    },
    "claude-haiku-4-5": {
        "input": 1.0,
        "output": 5.0,
        "cache_read": 0.10,
        "cache_write": 1.25,
    },
    # 上一代
    "claude-sonnet-4-5": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
    "claude-opus-4-5": {
        "input": 5.0,
        "output": 25.0,
        "cache_read": 0.50,
        "cache_write": 6.25,
    },
    "claude-opus-4-1": {
        "input": 15.0,
        "output": 75.0,
        "cache_read": 1.50,
        "cache_write": 18.75,
    },
    "claude-opus-4": {
        "input": 15.0,
        "output": 75.0,
        "cache_read": 1.50,
        "cache_write": 18.75,
    },
    "claude-sonnet-4": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
    "claude-3-haiku": {
        "input": 0.25,
        "output": 1.25,
        "cache_read": 0.03,
        "cache_write": 0.30,
    },
}

# DashScope / Qwen 模型每百万 token 的定价（硬编码）。
# 来源：https://help.aliyun.com/zh/dashscope/developer-reference/tongyi-qianwen-7b-14b-72b-pricing
DASHSCOPE_PRICING: dict[str, dict[str, float]] = {
    "qwen-plus": {
        "input": 0.004,
        "output": 0.012,
    },
    "qwen-plus-latest": {
        "input": 0.004,
        "output": 0.012,
    },
    "qwen-max": {
        "input": 0.04,
        "output": 0.12,
    },
    "qwen-max-latest": {
        "input": 0.04,
        "output": 0.12,
    },
    "qwen-turbo": {
        "input": 0.001,
        "output": 0.002,
    },
    "qwen-turbo-latest": {
        "input": 0.001,
        "output": 0.002,
    },
    "qwen-long": {
        "input": 0.001,
        "output": 0.002,
    },
    "qwen-vl-max": {
        "input": 0.008,
        "output": 0.024,
    },
    "qwen-vl-plus": {
        "input": 0.004,
        "output": 0.012,
    },
}

_PER_MILLION = 1_000_000.0


def _anthropic_cost(usage: Usage, model: str) -> float | None:
    """使用 Anthropic 硬编码定价计算成本。

    如果模型不是 Anthropic 模型则返回 None。
    使用前缀匹配（例如 "claude-sonnet-4-6-20250514" 匹配 "claude-sonnet-4-6"）。
    """
    prices = ANTHROPIC_PRICING.get(model)
    if prices is None:
        # 前缀匹配
        for key, p in ANTHROPIC_PRICING.items():
            if model.startswith(key):
                prices = p
                break
    if prices is None:
        return None
    return (
        usage.input_tokens * prices["input"]
        + usage.output_tokens * prices["output"]
        + usage.cache_read_input_tokens * prices.get("cache_read", 0.0)
        + usage.cache_creation_input_tokens * prices.get("cache_write", 0.0)
    ) / _PER_MILLION


_SENTINEL_NON_PRICED_MODELS = frozenset({
    # ScriptedProvider / RemoteScriptedProvider 使用的名称；并非真实模型，
    # litellm 对它们没有定价，若询问会向 stdout 输出一条嘈杂的横幅。
    "remote", "scripted-model",
})


def _litellm_cost(usage: Usage, model: str) -> float | None:
    """使用 litellm 的模型定价注册表计算成本。

    如果 litellm 不认识该模型或不可用则返回 None。
    当 litellm 无法解析模型时，会向 stdout 打印 "Provider List: ..." 横幅；
    我们抑制该输出，并对已知的仅用于测试（test-only）的模型名称做短路处理。
    """
    if not model or model.lower() in _SENTINEL_NON_PRICED_MODELS:
        return None
    import contextlib
    import io
    try:
        import litellm
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            prompt_cost, completion_cost = litellm.cost_per_token(
                model=model,
                prompt_tokens=usage.input_tokens,
                completion_tokens=usage.output_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
            )
        return prompt_cost + completion_cost
    except Exception:
        return None


def _dashscope_cost(usage: Usage, model: str) -> float | None:
    """使用 DashScope/Qwen 硬编码定价计算成本。

    如果模型不是 DashScope 模型则返回 None。
    使用前缀匹配（例如 "qwen-plus-latest" 匹配 "qwen-plus"）。
    """
    prices = DASHSCOPE_PRICING.get(model)
    if prices is None:
        for key, p in DASHSCOPE_PRICING.items():
            if model.startswith(key):
                prices = p
                break
    if prices is None:
        return None
    return (
        usage.input_tokens * prices["input"]
        + usage.output_tokens * prices["output"]
    ) / _PER_MILLION


class CostTracker:
    """跟踪 API 成本与用量。

    定价逻辑位于此处。累计总额存储在构造时传入的 ``SessionState`` 中
    （由磁盘支撑）。各模型明细与 API 耗时仅保存在内存中。
    """

    def __init__(self, session: SessionState) -> None:
        """绑定到 ``SessionState`` 以持久化成本总额。

        Args:
            session: 该跟踪器在每次 ``add_usage`` 调用时累加其磁盘持久化总额的会话。
        """
        self._session = session
        self.model_usage: dict[str, Usage] = {}
        self.total_api_duration_ms: float = 0.0

    def calculate_cost(self, usage: Usage, model: str) -> float | None:
        """计算单条 Usage 记录的预估美元成本。

        解析顺序：
        1. Anthropic 硬编码定价（精确，包含缓存定价）
        2. litellm 注册表（覆盖数百个模型）
        3. None（未知模型、自托管等）
        """
        # 先尝试 Anthropic 定价（对 Anthropic 模型最精确）
        cost = _anthropic_cost(usage, model)
        if cost is not None:
            return cost

        # 尝试 litellm 注册表
        cost = _litellm_cost(usage, model)
        if cost is not None:
            return cost

        # 尝试 DashScope 定价（覆盖 qwen-plus、qwen-max 等）
        return _dashscope_cost(usage, model)

    def add_usage(
        self, usage: Usage, model: str, duration_ms: float = 0.0
    ) -> None:
        """记录单次 API 调用的用量。

        更新会话状态（磁盘持久化）和内存中各模型的跟踪信息。
        """
        # 各模型跟踪（仅内存）
        if model not in self.model_usage:
            self.model_usage[model] = Usage()
        self.model_usage[model].accumulate(usage)
        self.total_api_duration_ms += duration_ms

        # 更新会话状态总额（磁盘持久化，批量合并为单次写入）
        with self._session.batch():
            self._session.total_input_tokens += usage.input_tokens
            self._session.total_output_tokens += usage.output_tokens
            self._session.total_cache_read_tokens += usage.cache_read_input_tokens
            self._session.total_cache_write_tokens += usage.cache_creation_input_tokens

            cost = self.calculate_cost(usage, model)
            if cost is not None:
                self._session.total_cost_usd += cost
            else:
                self._session.cost_unknown = True

    def get_summary(self) -> dict:
        """返回适合记录日志或展示的摘要字典。"""
        models: dict[str, dict] = {}
        for model_name, usage in self.model_usage.items():
            models[model_name] = {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_input_tokens": usage.cache_read_input_tokens,
                "cache_creation_input_tokens": usage.cache_creation_input_tokens,
                "cost_usd": self.calculate_cost(usage, model_name),
            }

        s = self._session
        return {
            "total_input_tokens": s.total_input_tokens,
            "total_output_tokens": s.total_output_tokens,
            "total_cache_read_tokens": s.total_cache_read_tokens,
            "total_cache_write_tokens": s.total_cache_write_tokens,
            "total_cost_usd": s.total_cost_usd,
            "total_api_duration_ms": self.total_api_duration_ms,
            "models": models,
        }
