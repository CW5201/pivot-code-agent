"""查询循环状态管理。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class LoopState:
    """在循环迭代之间传递的可变状态。"""
    messages: list  # 当前消息历史
    max_output_tokens_recovery_count: int = 0
    max_output_tokens_override: int | None = None
    has_attempted_emergency_compact: bool = False
    iteration_count: int = 0  # 已完成的工具使用迭代次数
    transition: str | None = None  # 上一轮迭代继续的原因
    auto_compact_tracking: dict | None = None  # {compacted, iteration_counter, consecutive_failures}
    turns_since_memory_update: int = 0  # 自上次记忆提醒以来的迭代次数（密集模式）
    cached_model_info: Any = None  # 来自提供者的缓存 ModelInfo（模型变更时重置）
    # 上次 API 调用由提供者报告的使用量，捕获它以便下一次
    # 迭代的调用前 token 估算可以将其作为下限。
    last_input_tokens: int = 0
    last_output_tokens: int = 0
    # 上次调用时刻 len(state.messages) 的值，以便我们可以
    # 增量统计自那时起新增的消息数量。
    messages_len_at_last_call: int = 0
