"""用于 AGT 导航的内存快照管理。

快照以 ``.pivot/memory/`` 目录的完整副本形式，
存储在 ``.pivot/memory_snapshots/<commit_sha>/`` 中。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def take_memory_snapshot(cwd: str, commit_sha: str) -> Path | None:
    """将 ``.pivot/memory/`` 复制为 ``.pivot/memory_snapshots/<sha>/``。

    返回快照目录；若内存目录不存在则返回 None。
    """
    src = Path(cwd) / ".pivot" / "memory"
    if not src.exists():
        return None

    dst = Path(cwd) / ".pivot" / "memory_snapshots" / commit_sha
    try:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        logger.debug("Memory snapshot taken: %s", commit_sha[:7])
        return dst
    except OSError as e:
        logger.warning("Failed to take memory snapshot: %s", e)
        return None


def restore_memory_snapshot(cwd: str, target_sha: str) -> bool:
    """从 ``.pivot/memory_snapshots/<sha>/`` 恢复内存。

    若不存在 *target_sha* 对应的快照，则沿 git 祖先回溯，
    寻找最近的快照。恢复成功返回 True，找不到快照则返回 False。
    """
    dst = Path(cwd) / ".pivot" / "memory"
    snap_dir = _find_snapshot(cwd, target_sha)
    if not snap_dir:
        logger.debug("No memory snapshot found for %s or ancestors", target_sha[:7])
        return False

    try:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(snap_dir, dst)
        logger.debug("Memory restored from snapshot: %s", snap_dir.name[:7])
        return True
    except OSError as e:
        logger.warning("Failed to restore memory snapshot: %s", e)
        return False


def get_memory_diff(cwd: str, sha1: str, sha2: str) -> str:
    """计算两个内存快照之间的文本差异。

    返回人类可读的差异；若无差异或快照缺失则返回空字符串。
    """
    snap1 = _snapshot_path(cwd, sha1)
    snap2 = _snapshot_path(cwd, sha2)

    if not snap1.exists() and not snap2.exists():
        return ""

    lines: list[str] = []

    # 收集两个快照中的所有文件
    files1 = _list_files(snap1) if snap1.exists() else {}
    files2 = _list_files(snap2) if snap2.exists() else {}
    all_files = sorted(set(files1.keys()) | set(files2.keys()))

    for rel_path in all_files:
        content1 = files1.get(rel_path, "")
        content2 = files2.get(rel_path, "")
        if content1 == content2:
            continue
        if not content1:
            lines.append(f"+ {rel_path} (new file)")
        elif not content2:
            lines.append(f"- {rel_path} (deleted)")
        else:
            lines.append(f"~ {rel_path} (modified)")

    return "\n".join(lines)


def _snapshot_path(cwd: str, sha: str) -> Path:
    return Path(cwd) / ".pivot" / "memory_snapshots" / sha


def _find_snapshot(cwd: str, sha: str, max_depth: int = 20) -> Path | None:
    """沿祖先回溯，寻找最近的内存快照。"""
    current = sha
    for _ in range(max_depth):
        snap = _snapshot_path(cwd, current)
        if snap.exists():
            return snap
        # 回溯到父节点
        try:
            result = subprocess.run(
                ["git", "rev-parse", f"{current}~1"],
                cwd=cwd, capture_output=True, text=True, timeout=5,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if result.returncode != 0:
                break
            current = result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            break
    return None


def _list_files(directory: Path) -> dict[str, str]:
    """列出目录树中所有文件及其内容。"""
    result: dict[str, str] = {}
    if not directory.exists():
        return result
    for root, _dirs, files in os.walk(directory):
        for f in files:
            full = Path(root) / f
            rel = str(full.relative_to(directory))
            try:
                result[rel] = full.read_text(errors="replace")
            except OSError:
                result[rel] = ""
    return result
