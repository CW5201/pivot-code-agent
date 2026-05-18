"""项目设置管理（.pivot/settings.json）。

实现配置优先级链：
1. CLI 参数 / PivotCodeAgent() 构造函数参数 — 始终优先
2. 项目设置（.pivot/settings.json） — 每个项目的默认值
3. Pivot Code 内置默认值 — 硬编码的后备值

首次在项目中使用时，.pivot/settings.json 将使用内置默认值生成。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── 内置默认值（基准真相） ─────────────────────────────────────
# 每个可配置参数必须出现在这里。这也用于初始化 .pivot/settings.json
# 以及在更新时填充缺失的字段。

SETTINGS_DEFAULTS: dict[str, Any] = {
    # 后端（传输方式）+ 模型
    # "backend"：哪种 Pivot 传输方式与模型通信。
    #   - "auto"：通用 — LiteLLM，支持任何提供商（通过模型字符串前缀，
    #     例如 ``ollama/llama3``、``openrouter/...``）。
    #   - "anthropic-native"：直接使用 Anthropic SDK。解锁 cache_control
    #     断点、原生思考和原生 tool_use。适用于裸 Claude 模型名称的正确选择。
    #   - "scripted"：用于测试的确定性提供商。
    # 当用户设置 ``model`` 而未显式指定 ``backend`` 时，
    # backend 将从模型字符串推断（参见 ``infer_backend``）。
    "backend": "auto",
    "model": "openai/agnes-2.5-flash",
    "api_key": None,  # None = 从环境变量读取
    "base_url": None,  # None = 使用提供商默认值。设置用于本地服务器（例如 http://localhost:8000/v1）
    "tool_call_format": None,  # 基于文本的工具调用格式："hermes"、"glm"、"pivot" 或 None（原生）
    # 会话
    "permission_mode": "edit",  # 'yolo'、'edit'、'safe'
    "max_iterations_per_turn": None, # None = 无限制。限制每条用户消息的 API 调用次数。
    "max_output_tokens": None,  # None = 提供商默认值
    # 系统提示词
    "custom_system_prompt": None,
    "append_system_prompt": None,
    # 记忆
    "memory": "off",  # "on"、"off"、"intensive"
    # 详细输出
    "verbose": False,
    # 钩子（生命周期事件钩子 — 参见 pivotcode/hooks/registry.py）
    "hooks": {},
    # Token / 上下文管理
    "compact_max_output_tokens": 20_000,  # 为压缩摘要输出预留的 Token 数
    "capped_default_max_tokens": 8_000,  # 默认 max_tokens（槽位预留优化）
    "escalated_max_tokens": 64_000,  # 达到上限默认值后的重试预算
    "auto_compact_buffer_tokens": 13_000,  # 触发自动压缩的上下文窗口下方缓冲区
    "warning_threshold_buffer_tokens": 20_000,  # 触发警告的剩余 Token 数
    "blocking_limit_buffer_tokens": 3_000,  # 硬性下限：低于此剩余量时拒绝调用 API
    "max_consecutive_compact_failures": 3,  # 自动压缩重试的熔断器
    "compaction_threshold_percent": 80,  # 触发压缩层的上下文窗口百分比
    "max_compact_ptl_retries": 3,  # 压缩摘要期间的最大"提示过长"重试次数
    # 错误恢复
    "max_output_tokens_recovery_limit": 3,  # 达到输出限制时的最大多轮恢复尝试次数
    # 工具执行
    "max_tool_concurrency": 10,  # 最大并行只读工具执行数
    "tool_result_max_chars": 20_000,  # 截断前的每个工具结果大小
    "compact_clear_keep_recent": 10,  # 在 Layer B（清除）期间保留的最近工具结果数量
    # 思考
    "thinking_budget_default": 10_000,  # 默认思考 Token 预算（当模型支持时）
    # 记忆
    "memory_reminder_threshold": 10,  # 记忆提醒之间的迭代次数（intensive 模式）
    "max_scratchpad_sessions": 5,  # 保留的最大 scratchpad 会话目录数
    # 压缩层开关
    "compaction_truncate_enabled": True,
    "compaction_clear_enabled": True,
    "compaction_auto_enabled": True,
}

# 不应写入 settings.json 的字段（临时 / 仅限每次调用）
_EPHEMERAL_FIELDS = {"api_key"}


def get_pivot_dir(cwd: str | None = None) -> Path:
    """获取给定工作目录的 .pivot/ 目录。"""
    base = Path(cwd) if cwd else Path.cwd()
    return base / ".pivot"


def get_settings_path(cwd: str | None = None) -> Path:
    """获取 .pivot/settings.json 的路径。"""
    return get_pivot_dir(cwd) / "settings.json"


def load_settings(cwd: str | None = None) -> dict[str, Any]:
    """从 .pivot/settings.json 加载项目设置。

    如果文件不存在或损坏/无效，返回空字典。
    """
    path = get_settings_path(cwd)
    if not path.exists():
        return {}

    try:
        with open(path) as f:
            settings = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to read {path}: {e}. Using defaults.")
        return {}

    if not isinstance(settings, dict):
        logger.warning(f"Invalid settings format in {path}. Using defaults.")
        return {}

    # 将任何旧版 ``provider`` 键翻译为新的 ``backend`` 键，
    # 这样旧版 .pivot/settings.json 文件无需手动编辑即可继续加载。
    if migrate_legacy_provider_key(settings):
        logger.info(
            "%s used the legacy 'provider' key — auto-migrated to 'backend'. "
            "Re-save settings to silence this notice (/settings backend=<value>).",
            path,
        )

    return settings


def save_settings(settings: dict[str, Any], cwd: str | None = None) -> None:
    """将设置写入 .pivot/settings.json。

    如需要则创建 .pivot/ 目录。排除临时字段。
    """
    path = get_settings_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)

    # 过滤掉临时字段
    to_write = {k: v for k, v in settings.items() if k not in _EPHEMERAL_FIELDS}

    try:
        from pivotcode.utils.atomic_io import atomic_write_json
        atomic_write_json(path, to_write, indent=2)
        logger.debug(f"Settings saved to {path}")
    except OSError as e:
        logger.warning(f"Failed to write {path}: {e}")


def load_projects_settings_and_maybe_init(cwd: str | None = None) -> dict[str, Any]:
    """确保 .pivot/settings.json 存在。

    如果不存在，则使用内置默认值创建。
    如果存在，则加载并返回。
    """
    path = get_settings_path(cwd)
    if not path.exists():
        logger.info(f"Initializing {path} with default settings")
        defaults = {
            k: v for k, v in SETTINGS_DEFAULTS.items() if k not in _EPHEMERAL_FIELDS
        }
        save_settings(defaults, cwd)
        return dict(SETTINGS_DEFAULTS)

    return load_settings(cwd)


def coerce_value(raw: str) -> Any:
    """将 CLI 字符串值自动强制转换为适当的 Python 类型。"""
    lower = raw.lower()
    if lower in ("true", "yes", "y"):
        return True
    if lower in ("false", "no"):
        return False
    if lower in ("null", "none", ""):
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


# ── 设置验证器 ──────────────────────────────────────────────────────
# 每个条目为 (check_fn, error_message)。
# - check_fn(value) -> bool：如果有效则返回 True
# - None 值始终通过（表示"未设置"）
# - 没有条目的键不会被验证。

_one_of = lambda *vals: (lambda v: v in vals, f"Must be one of: {', '.join(repr(v) for v in vals)}")
_is_str = (lambda v: isinstance(v, str), "Must be a string")
_is_bool = (lambda v: isinstance(v, bool), "Must be a boolean")
_is_pos_int = (lambda v: isinstance(v, int) and v > 0, "Must be a positive integer")
_is_pos_int_or_none = (lambda v: v is None or (isinstance(v, int) and v > 0), "Must be a positive integer or null")

SETTING_VALIDATORS: dict[str, tuple] = {
    "backend": _one_of("auto", "anthropic-native", "scripted"),
    "model": _is_str,
    "base_url": _is_str,
    "tool_call_format": _one_of("hermes", "glm", "pivot"),
    "permission_mode": _one_of("yolo", "edit", "safe"),
    "max_iterations_per_turn": _is_pos_int_or_none,
    "max_output_tokens": _is_pos_int_or_none,
    "custom_system_prompt": _is_str,
    "append_system_prompt": _is_str,
    "memory": _one_of("on", "off", "intensive"),
    "verbose": _is_bool,
    "compact_max_output_tokens": _is_pos_int,
    "capped_default_max_tokens": _is_pos_int,
    "escalated_max_tokens": _is_pos_int,
    "auto_compact_buffer_tokens": _is_pos_int,
    "warning_threshold_buffer_tokens": _is_pos_int,
    "blocking_limit_buffer_tokens": _is_pos_int,
    "max_consecutive_compact_failures": _is_pos_int,
    "compaction_threshold_percent": (lambda v: isinstance(v, int) and 20 <= v <= 99, "Must be an integer between 20 and 99"),
    "max_compact_ptl_retries": _is_pos_int,
    "max_output_tokens_recovery_limit": _is_pos_int,
    "max_tool_concurrency": _is_pos_int,
    "tool_result_max_chars": _is_pos_int,
    "compact_clear_keep_recent": _is_pos_int,
    "thinking_budget_default": _is_pos_int,
    "memory_reminder_threshold": _is_pos_int,
    "max_scratchpad_sessions": _is_pos_int,
    "compaction_truncate_enabled": _is_bool,
    "compaction_clear_enabled": _is_bool,
    "compaction_auto_enabled": _is_bool,
}


def validate_setting(key: str, value: Any) -> str | None:
    """根据验证器验证设置值。

    如果无效则返回错误消息，如果有效则返回 None。
    None 值始终通过（表示"未设置"）。
    """
    entry = SETTING_VALIDATORS.get(key)
    if entry is None:
        return None  # 此键没有验证器
    check_fn, error_msg = entry
    if value is None:
        return None  # None 始终被接受
    if not check_fn(value):
        return f"Invalid value {value!r} for '{key}': {error_msg}"
    return None


# 在会话中更改时会触发后端（LLMProvider）重建的设置。
BACKEND_SETTINGS: set[str] = {
    "backend",
    "model",
    "api_key",
    "base_url",
}

# 向后兼容的别名 — 为外部代码（例如测试、下游工具）保留一个版本，
# 因为它们可能导入了旧名称。
PROVIDER_SETTINGS = BACKEND_SETTINGS


# ── 后端推断和旧键迁移 ──────────────────────────────────────


# 旧版 --provider 值 → 新的 backend 名称。
_LEGACY_PROVIDER_MAP: dict[str, str] = {
    "litellm": "auto",
    "anthropic": "anthropic-native",
    "scripted": "scripted",
}


def infer_backend(model: str | None) -> str:
    """从模型字符串推断后端。

    所有模型使用通用 LiteLLM 传输（backend="auto"）。
    当 ``model`` 为 ``None`` 或空时返回 ``"auto"``。
    """
    return "auto"


def migrate_legacy_provider_key(settings: dict[str, Any]) -> bool:
    """将旧版 ``provider`` 键翻译为新的 ``backend`` 键。

    就地修改 *settings*。如果发生任何更改则返回 ``True``
    （调用者可能想要记录弃用通知）。

    映射关系：
        ``provider="litellm"``   → ``backend="auto"``
        ``provider="anthropic"`` → ``backend="anthropic-native"``
        ``provider="scripted"``  → ``backend="scripted"``

    任何其他旧版值将被静默丢弃；调用者应在其他地方
    提供友好的错误信息。
    """
    if "provider" not in settings:
        return False

    old = settings.pop("provider")
    if "backend" in settings:
        # 用户已指定新键；旧版值已过时。
        return True

    if isinstance(old, str):
        mapped = _LEGACY_PROVIDER_MAP.get(old.lower())
        if mapped is not None:
            settings["backend"] = mapped
            return True

    # 未知的旧版值 — 保持 backend 未设置（调用者将回退到默认值或推断）。
    return True
