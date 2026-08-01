"""与磁盘关联的会话状态。

SessionState 是所有持久化会话数据的唯一真实来源。
每次读取属性都来自内存缓存；每次写入属性都会将缓存刷新到磁盘上的
``state.json``。这保证了无需显式 save/restore 调用也能具备崩溃恢复能力。

使用 :meth:`batch` 可将多次更新合并为单次磁盘写入::

    with session.batch():
        session.turn_count += 1
        session.total_cost_usd += delta
"""

from __future__ import annotations

import errno
import json
import logging

try:
    import msvcrt
    HAS_MSVC = True
except ImportError:
    HAS_MSVC = False
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pivotcode.utils.atomic_io import atomic_write_json

logger = logging.getLogger(__name__)


def _get_session_state_path(cwd: str, session_id: str) -> Path:
    """返回 ``.pivot/sessions/<session_id>/state.json``。"""
    return Path(cwd) / ".pivot" / "sessions" / session_id / "state.json"


class SessionLockedError(RuntimeError):
    """当会话已被另一个进程占用时抛出。

    否则两个进程加载同一个 ``session_id`` 会互相覆盖对方对 ``state.json``
    和 ``transcript.jsonl`` 的写入。
    """

    def __init__(self, session_id: str, lock_path: Path, holder_info: str) -> None:
        super().__init__(
            f"Session {session_id[:8]} is already in use by another process. "
            f"Holder: {holder_info}. Lock file: {lock_path}"
        )
        self.session_id = session_id
        self.lock_path = lock_path
        self.holder_info = holder_info


def _acquire_session_lock(session_dir: Path, session_id: str) -> int:
    """获取 ``<session_dir>/session.lock`` 的独占锁。

    跨平台：在 Windows 上使用 ``msvcrt``，在 Unix 上使用 ``fcntl``。
    返回打开的文件描述符；当该 fd 关闭（或进程退出）时，内核会释放锁。
    如果另一个存活进程持有该锁，则抛出 :class:`SessionLockedError`。
    """
    lock_path = session_dir / "session.lock"
    holder_path = session_dir / "session.holder"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    if HAS_MSVC:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            try:
                with open(holder_path, "r", encoding="utf-8") as f:
                    holder_info = f.read().strip() or "<unknown>"
            except OSError:
                holder_info = "<unknown>"
            os.close(fd)
            raise SessionLockedError(session_id, lock_path, holder_info)
    else:
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as e:
            if getattr(e, "errno", None) not in (errno.EWOULDBLOCK, errno.EAGAIN):
                os.close(fd)
                raise
            try:
                with open(holder_path, "r", encoding="utf-8") as f:
                    holder_info = f.read().strip() or "<unknown>"
            except OSError:
                holder_info = "<unknown>"
            os.close(fd)
            raise SessionLockedError(session_id, lock_path, holder_info)

    os.ftruncate(fd, 0)
    info = (
        f"pid={os.getpid()} "
        f"acquired_at={datetime.now(UTC).isoformat()}\n"
    )
    os.write(fd, info.encode("utf-8"))
    try:
        with open(holder_path, "w", encoding="utf-8") as f:
            f.write(info)
    except OSError:
        pass
    return fd


class SessionState:
    """与磁盘关联的会话状态。

    只有 ``session_id`` 和 ``cwd`` 是普通属性。所有其他字段都是基于
    内存缓存的属性，每次写入都会刷新到 ``.pivot/sessions/<id>/state.json``。
    """

    def __init__(self, session_id: str, cwd: str) -> None:
        """打开（或创建）会话状态文件并加载其缓存。

        如果 ``.pivot/sessions/<session_id>/`` 不存在则创建它。
        每次读取属性都来自内存中的 ``_cache``；每个 setter 都会调用
        ``_flush()`` 以原子方式持久化。

        Args:
            session_id: 十六进制会话标识符。
            cwd: 项目工作目录 —— `.pivot/` 位于此处。
        """
        self.session_id = session_id
        self.cwd = cwd
        self._state_path = _get_session_state_path(cwd, session_id)
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_fd: int | None = _acquire_session_lock(
            self._state_path.parent, session_id,
        )
        self._cache: dict[str, Any] = self._load_from_disk()
        self._batch_depth: int = 0

    def close(self) -> None:
        """释放会话锁。幂等。"""
        if self._lock_fd is None:
            return
        if not HAS_MSVC:
            try:
                import fcntl
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            os.close(self._lock_fd)
        except OSError:
            pass
        self._lock_fd = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # ── 磁盘 I/O ──────────────────────────────────────────────────────────

    def _load_from_disk(self) -> dict[str, Any]:
        """将 ``state.json`` 读取为字典，若未找到或出错则返回 ``{}``。

        损坏的文件会以 WARNING 级别记录日志并视为空 —— 我们宁可丢失会话
        状态，也不愿让会话启动崩溃。``_flush`` 中的原子写入规范本来就使
        部分写入导致的损坏极为罕见。

        Returns:
            解析后的 JSON 对象；若文件缺失、不可读或不是字典，则返回 ``{}``。
        """
        if not self._state_path.exists():
            return {}
        try:
            with open(self._state_path) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load session state %s: %s", self._state_path, e)
            return {}

    def _flush(self) -> None:
        """以原子方式将缓存写入磁盘（在 batch 内时跳过）。"""
        if self._batch_depth > 0:
            return
        try:
            atomic_write_json(self._state_path, self._cache, indent=2)
        except OSError as e:
            logger.warning("Failed to write session state %s: %s", self._state_path, e)

    @contextmanager
    def batch(self):
        """将多次更新合并为单次磁盘写入。

        示例::

            with session.batch():
                session.total_input_tokens += 100
                session.total_output_tokens += 50
                session.total_cost_usd += 0.01
            # 在此处一次性刷新
        """
        self._batch_depth += 1
        try:
            yield
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                self._flush()

    # ── 内部辅助函数 ──────────────────────────────────────────────────────────

    def _get(self, key: str, default: Any = None) -> Any:
        return self._cache.get(key, default)

    def _set(self, key: str, value: Any) -> None:
        self._cache[key] = value
        self._flush()

    # ── 属性 ────────────────────────────────────────────────────────

    @property
    def turn_count(self) -> int:
        return self._get("turn_count", 0)

    @turn_count.setter
    def turn_count(self, value: int) -> None:
        self._set("turn_count", value)

    @property
    def total_cost_usd(self) -> float:
        return self._get("total_cost_usd", 0.0)

    @total_cost_usd.setter
    def total_cost_usd(self, value: float) -> None:
        self._set("total_cost_usd", value)

    @property
    def total_input_tokens(self) -> int:
        return self._get("total_input_tokens", 0)

    @total_input_tokens.setter
    def total_input_tokens(self, value: int) -> None:
        self._set("total_input_tokens", value)

    @property
    def total_output_tokens(self) -> int:
        return self._get("total_output_tokens", 0)

    @total_output_tokens.setter
    def total_output_tokens(self, value: int) -> None:
        self._set("total_output_tokens", value)

    @property
    def total_cache_read_tokens(self) -> int:
        return self._get("total_cache_read_tokens", 0)

    @total_cache_read_tokens.setter
    def total_cache_read_tokens(self, value: int) -> None:
        self._set("total_cache_read_tokens", value)

    @property
    def total_cache_write_tokens(self) -> int:
        return self._get("total_cache_write_tokens", 0)

    @total_cache_write_tokens.setter
    def total_cache_write_tokens(self, value: int) -> None:
        self._set("total_cache_write_tokens", value)

    @property
    def cost_unknown(self) -> bool:
        return self._get("cost_unknown", False)

    @cost_unknown.setter
    def cost_unknown(self, value: bool) -> None:
        self._set("cost_unknown", value)

    # 上次 API 调用上报的用量。已持久化，以便恢复后的会话能基于用量为其
    # 首次调用前的压缩估算提供下限，并能在任何新调用完成前显示
    # "Conversation: N / M" 数值。每次 API 调用后在内存中刷新；
    # 在轮次边界处保存到磁盘。
    @property
    def last_input_tokens(self) -> int:
        return self._get("last_input_tokens", 0)

    @last_input_tokens.setter
    def last_input_tokens(self, value: int) -> None:
        self._set("last_input_tokens", value)

    @property
    def last_output_tokens(self) -> int:
        return self._get("last_output_tokens", 0)

    @last_output_tokens.setter
    def last_output_tokens(self, value: int) -> None:
        self._set("last_output_tokens", value)

    @property
    def last_cache_read_tokens(self) -> int:
        return self._get("last_cache_read_tokens", 0)

    @last_cache_read_tokens.setter
    def last_cache_read_tokens(self, value: int) -> None:
        self._set("last_cache_read_tokens", value)

    @property
    def last_cache_write_tokens(self) -> int:
        return self._get("last_cache_write_tokens", 0)

    @last_cache_write_tokens.setter
    def last_cache_write_tokens(self, value: int) -> None:
        self._set("last_cache_write_tokens", value)

    @property
    def session_name(self) -> str:
        """用户指定的会话名称（通过 /name 命令）。未设置时为空。"""
        return self._get("session_name", "")

    @session_name.setter
    def session_name(self, value: str) -> None:
        self._set("session_name", value)

    # 允许规则是项目级作用域（在项目内的各会话之间持久化）。
    # 存储在 .pivot/allow_rules.json —— 参见 pivotcode/permissions/project_rules.py。
    @property
    def allow_rules(self) -> list[dict[str, Any]]:
        """读取项目级作用域的允许规则列表。

        在会话中首次访问时，会将 ``state.json`` 中任何遗留的会话级
        ``allow_rules`` 条目迁移到项目文件中，以便旧会话不会丢失其规则。

        Returns:
            规则字典的列表（``tool_name``、``rule_content``、``source``）。
            参见 :mod:`pivotcode.permissions.project_rules`。
        """
        from pivotcode.permissions.project_rules import load_project_allow_rules
        # 一次性迁移：如果旧会话在 state.json 中有规则，
        # 将其移入项目文件，然后删除会话级字段。
        legacy = self._cache.get("allow_rules")
        if legacy:
            from pivotcode.permissions.project_rules import (
                load_project_allow_rules as _load,
            )
            from pivotcode.permissions.project_rules import (
                save_project_allow_rules as _save,
            )
            existing = _load(self.cwd)
            seen = {(r.get("tool_name"), r.get("rule_content")) for r in existing}
            for r in legacy:
                key = (r.get("tool_name"), r.get("rule_content"))
                if key not in seen:
                    existing.append(r)
                    seen.add(key)
            _save(existing, self.cwd)
            self._cache.pop("allow_rules", None)
            self._flush()
        return load_project_allow_rules(self.cwd)

    @allow_rules.setter
    def allow_rules(self, value: list[dict[str, Any]]) -> None:
        from pivotcode.permissions.project_rules import save_project_allow_rules
        save_project_allow_rules(value, self.cwd)

    def add_allow_rule(self, rule_dict: dict[str, Any]) -> None:
        """向项目级存储追加一条允许规则。"""
        from pivotcode.permissions.project_rules import add_project_allow_rule
        add_project_allow_rule(rule_dict, self.cwd)

    # ── AGT（Agentic Git Tree）属性 ────────────────────────────────

    @property
    def pivot_commits(self) -> list[str]:
        """由 agent 通过 GitCommit 工具生成的提交的 SHA。"""
        return list(self._get("pivot_commits", []))

    @pivot_commits.setter
    def pivot_commits(self, value: list[str]) -> None:
        self._set("pivot_commits", value)

    def add_pivot_commit(self, sha: str) -> None:
        """追加一个提交 SHA 并刷新到磁盘。"""
        commits = self._cache.get("pivot_commits", [])
        commits.append(sha)
        self._set("pivot_commits", commits)

    @property
    def conv_path(self) -> list[str]:
        """agent 访问过的提交 SHA 的有序列表（对话路径）。"""
        return list(self._get("conv_path", []))

    @conv_path.setter
    def conv_path(self, value: list[str]) -> None:
        self._set("conv_path", value)

    def add_to_conv_path(self, sha: str) -> None:
        """向对话路径追加一个 SHA 并刷新。"""
        path = self._cache.get("conv_path", [])
        path.append(sha)
        self._set("conv_path", path)

    @property
    def compaction_markers(self) -> list[str]:
        """每次压缩时 HEAD 的 SHA。"""
        return list(self._get("compaction_markers", []))

    @compaction_markers.setter
    def compaction_markers(self, value: list[str]) -> None:
        self._set("compaction_markers", value)

    def add_compaction_marker(self, sha: str) -> None:
        """记录压缩发生在该提交处。"""
        markers = self._cache.get("compaction_markers", [])
        markers.append(sha)
        self._set("compaction_markers", markers)

    @property
    def session_root_sha(self) -> str:
        """会话开始时的 HEAD 的 SHA。"""
        return self._get("session_root_sha", "")

    @session_root_sha.setter
    def session_root_sha(self, value: str) -> None:
        self._set("session_root_sha", value)

    @property
    def agent_position_sha(self) -> str:
        """agent 当前所在提交的 SHA。"""
        return self._get("agent_position_sha", "")

    @agent_position_sha.setter
    def agent_position_sha(self, value: str) -> None:
        self._set("agent_position_sha", value)

    @property
    def commit_message_indices(self) -> dict[str, int]:
        """将提交 SHA 映射到提交时的消息列表长度。

        供 /convrevert 使用，以确切知道应在何处截断消息。
        """
        return dict(self._get("commit_message_indices", {}))

    @commit_message_indices.setter
    def commit_message_indices(self, value: dict[str, int]) -> None:
        self._set("commit_message_indices", value)

    def record_commit_message_index(self, sha: str, message_count: int) -> None:
        """记录创建该提交时存在的消息数量。"""
        indices = self._cache.get("commit_message_indices", {})
        indices[sha] = message_count
        self._set("commit_message_indices", indices)
