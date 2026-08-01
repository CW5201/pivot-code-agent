"""会话管理 —— 查找、创建与解析会话。"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

# 不应写入会话设置的字段（仅临时 / 单次调用使用）
_EPHEMERAL_FIELDS = {"api_key"}


# ── 路径辅助函数 ──────────────────────────────────────────────────────────────


def get_sessions_dir(cwd: str) -> Path:
    """返回给定工作目录下的 ``.pivot/sessions/``。"""
    return Path(cwd) / ".pivot" / "sessions"


def get_session_dir(cwd: str, session_id: str) -> Path:
    """返回 ``.pivot/sessions/<session_id>/``。"""
    return get_sessions_dir(cwd) / session_id


def generate_session_id() -> str:
    """生成一个新的随机会话 ID。"""
    return uuid4().hex


# ── 会话查找 ────────────────────────────────────────────────────────────


def find_session_by_prefix(cwd: str, prefix: str) -> str | None:
    """按前缀查找会话 ID（至少 3 个字符）。若无匹配或超过 1 个匹配则返回 None。"""
    if len(prefix) < 3:
        return None
    sessions_dir = get_sessions_dir(cwd)
    if not sessions_dir.exists():
        return None
    matches = [d.name for d in sessions_dir.iterdir() if d.is_dir() and d.name.startswith(prefix)]
    return matches[0] if len(matches) == 1 else None


def get_last_session_id(cwd: str) -> str | None:
    """按 transcript 修改时间查找最近的会话 ID。

    扫描 *cwd* 下的 ``.pivot/sessions/*/transcript.jsonl``。

    如果不存在匹配的会话文件则返回 ``None``。
    """
    sessions_dir = get_sessions_dir(cwd)
    if not sessions_dir.is_dir():
        return None

    # 收集所有包含 transcript.jsonl 的会话目录
    candidates: list[tuple[str, float]] = []
    for session_dir in sessions_dir.iterdir():
        if not session_dir.is_dir():
            continue
        transcript = session_dir / "transcript.jsonl"
        if transcript.is_file():
            candidates.append((session_dir.name, transcript.stat().st_mtime))

    if not candidates:
        return None

    # 按修改时间降序排序
    candidates.sort(key=lambda x: x[1], reverse=True)

    norm_cwd = os.path.normpath(cwd)
    for session_id, _ in candidates:
        transcript = sessions_dir / session_id / "transcript.jsonl"
        try:
            with open(transcript, "r", encoding="utf-8") as fh:
                first_line = fh.readline().strip()
            if not first_line:
                continue
            d = json.loads(first_line)
            meta = d.get("_metadata")
            if meta is None:
                continue
            session_cwd = os.path.normpath(meta.get("cwd", ""))
            if session_cwd == norm_cwd:
                return session_id
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            # 损坏的 transcript 文件会悄悄地把该会话从列表中隐藏，导致用户
            # 明明知道自己有会话却看到"无会话"。记录日志以便他们能找到它。
            logger.warning(
                "Skipping session %s: transcript unreadable (%s)",
                session_id, exc,
            )
            continue

    return None


# ── 会话设置 ──────────────────────────────────────────────────────────


def get_session_settings_path(cwd: str, session_id: str) -> Path:
    """返回 ``.pivot/sessions/<session_id>/settings.json``。"""
    return get_session_dir(cwd, session_id) / "settings.json"


def load_session_settings(cwd: str, session_id: str) -> dict[str, Any]:
    """加载特定会话的设置。如果未找到则返回空字典。"""
    path = get_session_settings_path(cwd, session_id)
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_session_settings(cwd: str, session_id: str, settings: dict[str, Any]) -> None:
    """以原子方式保存会话的设置快照。"""
    from pivotcode.utils.atomic_io import atomic_write_json
    path = get_session_settings_path(cwd, session_id)
    to_write = {k: v for k, v in settings.items() if k not in _EPHEMERAL_FIELDS}
    atomic_write_json(path, to_write, indent=2)



# 注意：会话状态的持久化（turn_count、cost、allow_rules 等）
# 由 session/state.py 中的 SessionState 负责（与磁盘关联的属性）。
