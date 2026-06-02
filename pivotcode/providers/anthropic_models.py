"""Anthropic 模型注册表 — 所有已知 Claude 模型的能力信息。

由 AnthropicProvider.get_model_info() 调用，返回准确的 ModelInfo。
查找逻辑先尝试精确匹配，再尝试前缀匹配（因此带日期的模型 ID
如 ``claude-sonnet-4-6-20260401`` 可匹配 ``claude-sonnet-4-6``）。
"""

from pivotcode.providers.base import ModelInfo

ANTHROPIC_MODELS: dict[str, ModelInfo] = {
    # ── 当前活跃模型 ─────────────────────────────────────────────────────
    "claude-opus-4-7": ModelInfo(
        context_window=1_000_000,
        max_output_tokens=128_000,
        supports_thinking=True,
    ),
    "claude-opus-4-6": ModelInfo(
        context_window=1_000_000,
        max_output_tokens=128_000,
        supports_thinking=True,
    ),
    "claude-sonnet-4-6": ModelInfo(
        context_window=1_000_000,
        max_output_tokens=64_000,
        supports_thinking=True,
    ),
    "claude-sonnet-4-5": ModelInfo(
        context_window=200_000,
        max_output_tokens=64_000,
        supports_thinking=True,
    ),
    "claude-opus-4-5": ModelInfo(
        context_window=200_000,
        max_output_tokens=64_000,
        supports_thinking=True,
    ),
    "claude-opus-4-1": ModelInfo(
        context_window=200_000,
        max_output_tokens=32_000,
        supports_thinking=True,
    ),
    "claude-haiku-4-5": ModelInfo(
        context_window=200_000,
        max_output_tokens=64_000,
        supports_thinking=True,
    ),
    # ── 已弃用（仍可用，计划退役） ────────────────────────────────────────
    "claude-sonnet-4-20250514": ModelInfo(
        context_window=200_000,
        max_output_tokens=64_000,
        supports_thinking=True,
    ),
    "claude-opus-4-20250514": ModelInfo(
        context_window=200_000,
        max_output_tokens=32_000,
        supports_thinking=True,
    ),
    "claude-3-haiku-20240307": ModelInfo(
        context_window=200_000,
        max_output_tokens=4_096,
        supports_thinking=False,
    ),
    # ── 已退役（保留以向后兼容） ──────────────────────────────────────────
    "claude-3-7-sonnet-20250219": ModelInfo(
        context_window=200_000,
        max_output_tokens=128_000,
        supports_thinking=True,
    ),
    "claude-3-5-haiku-20241022": ModelInfo(
        context_window=200_000,
        max_output_tokens=8_192,
        supports_thinking=False,
    ),
    "claude-3-5-sonnet-20241022": ModelInfo(
        context_window=200_000,
        max_output_tokens=8_192,
        supports_thinking=False,
    ),
    "claude-3-5-sonnet-20240620": ModelInfo(
        context_window=200_000,
        max_output_tokens=8_192,
        supports_thinking=False,
    ),
    "claude-3-opus-20240229": ModelInfo(
        context_window=200_000,
        max_output_tokens=4_096,
        supports_thinking=False,
    ),
    "claude-3-sonnet-20240229": ModelInfo(
        context_window=200_000,
        max_output_tokens=4_096,
        supports_thinking=False,
    ),
    # ── 旧版（Claude-3 之前，无视觉/工具功能） ──────────────────────────────
    "claude-2.1": ModelInfo(
        context_window=200_000,
        max_output_tokens=4_096,
        supports_thinking=False,
    ),
    "claude-2.0": ModelInfo(
        context_window=100_000,
        max_output_tokens=4_096,
        supports_thinking=False,
    ),
    "claude-instant-1.2": ModelInfo(
        context_window=100_000,
        max_output_tokens=4_096,
        supports_thinking=False,
    ),
}


# 别名：将备选模型 ID 映射到其规范注册键。
ANTHROPIC_ALIASES: dict[str, str] = {
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
    "claude-sonnet-4-5-20250929": "claude-sonnet-4-5",
    "claude-opus-4-5-20251101": "claude-opus-4-5",
    "claude-opus-4-1-20250805": "claude-opus-4-1",
    "claude-sonnet-4-0": "claude-sonnet-4-20250514",
    "claude-opus-4-0": "claude-opus-4-20250514",
}


def lookup_anthropic_model(model: str) -> ModelInfo:
    """通过 ID 查找模型，支持别名解析和前缀匹配。

    解析顺序：
    1. 在 ANTHROPIC_MODELS 中精确匹配
    2. 通过 ANTHROPIC_ALIASES 解析别名后精确匹配
    3. 前缀匹配（例如 ``claude-sonnet-4-6-20260401`` 匹配 ``claude-sonnet-4-6``）
    4. 回退到安全默认值（200K 上下文，8K 输出，无思考模式）
    """
    # 精确匹配
    if model in ANTHROPIC_MODELS:
        return ANTHROPIC_MODELS[model]

    # 别名解析
    canonical = ANTHROPIC_ALIASES.get(model)
    if canonical and canonical in ANTHROPIC_MODELS:
        return ANTHROPIC_MODELS[canonical]

    # 前缀匹配（处理带日期后缀的模型如 claude-sonnet-4-6-20260401）
    for key, info in ANTHROPIC_MODELS.items():
        if model.startswith(key):
            return info

    # 未知模型 — 使用安全默认值
    return ModelInfo()
