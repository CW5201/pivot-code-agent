"""记忆目录管理 — PIVOT.md、MEMORY.md、scratchpad 等。"""

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

MEMORY_MD = "MEMORY.md"
PIVOT_MD = "PIVOT.md"
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000

# .pivot/memory/ 下的子目录（项目级作用域）
PROJECT_MEMORY_SUBDIRS = ("project", "reference", "workflow")
# ~/.pivot/memory/ 下的子目录（全局，跨项目共享）
GLOBAL_MEMORY_SUBDIRS = ("user", "feedback")
# 所有子目录（为了向后兼容）
MEMORY_SUBDIRS = PROJECT_MEMORY_SUBDIRS + GLOBAL_MEMORY_SUBDIRS


PIVOT_MD_TEMPLATE = """\
# Project Instructions

<!-- This file is read by Pivot Code at the start of every session. -->
<!-- Use it to give Pivot context about your project, preferences, and conventions. -->
<!-- For long term and/or autonomous project memory, consider using the memory option instead. -->

## Project overview

<!-- Describe your project here. What does it do? What technologies does it use? -->

## Conventions

<!-- List coding conventions, naming patterns, or style preferences. -->
<!-- Example: "Use Google-style docstrings", "Prefer pathlib over os.path" -->

## Important files

<!-- Point Pivot to key files or directories it should know about. -->
"""


def ensure_project_instructions(cwd: str) -> str:
    """确保项目根目录下存在 PIVOT.md。如果缺失则用起始模板创建。

    返回该文件的绝对路径。
    """
    path = Path(cwd).resolve() / PIVOT_MD
    if not path.exists():
        try:
            path.write_text(PIVOT_MD_TEMPLATE, encoding="utf-8")
            logger.info("Created %s with starter template", path)
        except OSError as exc:
            logger.warning("Failed to create %s: %s", path, exc)
    return str(path)


def find_project_instructions(cwd: str) -> str | None:
    """在 *cwd* 中查找 PIVOT.md。

    如果工作目录中存在 ``PIVOT.md`` 则返回其绝对路径，否则返回 ``None``。
    """
    candidate = Path(cwd).resolve() / PIVOT_MD
    if candidate.is_file():
        return str(candidate)
    return None


def load_project_instructions(cwd: str) -> str | None:
    """在 PIVOT.md 存在时加载其内容。

    从 *cwd* 出发，使用 :func:`find_project_instructions` 向上查找，
    读取文件并将其截断到安全限制内。如果没有找到 ``PIVOT.md`` 则返回 ``None``。
    """
    path = find_project_instructions(cwd)
    if path is None:
        return None
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return None

    content = truncate_content(content)
    return (
        f"# Project instructions ({PIVOT_MD})\n\n"
        f"The following is loaded from {path}:\n\n"
        f"{content}"
    )


def load_global_project_instructions() -> str | None:
    """从 ``~/.pivot/PIVOT.md`` 加载全局 PIVOT.md。

    如果没有全局指令则返回格式化后的段落或 None。
    全局指令提供跨项目的用户偏好。
    """
    path = Path.home() / ".pivot" / PIVOT_MD
    if not path.is_file():
        return None

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Failed to read global %s: %s", path, exc)
        return None

    if not content.strip():
        return None

    content = truncate_content(content)
    return f"# Global user instructions (~/.pivot/{PIVOT_MD})\n\n" f"{content}"


def truncate_content(
    content: str,
    *,
    max_lines: int = MAX_ENTRYPOINT_LINES,
    max_bytes: int = MAX_ENTRYPOINT_BYTES,
) -> str:
    """将 *content* 截断到行数和字节数限制。

    先应用行数限制，再应用字节数限制。如果发生截断，
    会在末尾追加一段提示说明。
    """
    lines = content.splitlines(keepends=True)
    truncated = False

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True

    result = "".join(lines)

    encoded = result.encode("utf-8")
    if len(encoded) > max_bytes:
        # Decode back safely to avoid splitting a multi-byte character
        result = encoded[:max_bytes].decode("utf-8", errors="ignore")
        truncated = True

    if truncated:
        result += "\n\n[... truncated]"

    return result


# ── 记忆目录 ───────────────────────────────────────────────────────────────────


def get_memory_dir(cwd: str | None = None) -> Path:
    """获取项目记忆目录路径（``.pivot/memory/``）。"""
    effective_cwd = cwd or os.getcwd()
    return Path(effective_cwd) / ".pivot" / "memory"


def get_global_memory_dir() -> Path:
    """获取全局记忆目录路径（``~/.pivot/memory/``）。"""
    return Path.home() / ".pivot" / "memory"


def ensure_memory_structure(cwd: str) -> Path:
    """同时创建项目级和全局的记忆目录树。

    项目记忆（``.pivot/memory/``）：project、reference、workflow 子目录。
    全局记忆（``~/.pivot/memory/``）：user、feedback 子目录。

    返回项目记忆根目录。
    """
    # 项目记忆
    mem_dir = get_memory_dir(cwd)
    mem_dir.mkdir(parents=True, exist_ok=True)
    for subdir in PROJECT_MEMORY_SUBDIRS:
        (mem_dir / subdir).mkdir(exist_ok=True)

    # 全局记忆
    global_dir = get_global_memory_dir()
    global_dir.mkdir(parents=True, exist_ok=True)
    for subdir in GLOBAL_MEMORY_SUBDIRS:
        (global_dir / subdir).mkdir(exist_ok=True)

    return mem_dir


# ── Scratchpad（临时草稿区） ────────────────────────────────────────────────────


def get_scratchpad_dir(cwd: str, session_id: str) -> Path:
    """获取某个特定会话的 scratchpad 目录。

    返回 ``.pivot/sessions/<session_id>/scratchpad``。
    """
    return Path(cwd) / ".pivot" / "sessions" / session_id / "scratchpad"


def cleanup_old_scratchpads(cwd: str, max_sessions: int = 5) -> None:
    """如果 scratchpad 目录数量超过 *max_sessions*，则移除最旧的目录。

    扫描 ``.pivot/sessions/*/scratchpad/`` 下的会话 scratchpad。
    目录按修改时间排序；最旧的会先被移除。
    同时也会清理旧的 ``.pivot/scratchpad/`` 目录。
    """
    # 新布局：.pivot/sessions/*/scratchpad/
    sessions_root = Path(cwd) / ".pivot" / "sessions"
    if sessions_root.is_dir():
        scratch_dirs = []
        for session_dir in sessions_root.iterdir():
            if not session_dir.is_dir():
                continue
            scratch = session_dir / "scratchpad"
            if scratch.is_dir():
                scratch_dirs.append(scratch)

        if len(scratch_dirs) > max_sessions:
            scratch_dirs.sort(key=lambda d: d.stat().st_mtime)
            to_remove = scratch_dirs[: len(scratch_dirs) - max_sessions]
            for d in to_remove:
                # 安全检查：只删除 sessions 目录范围内的路径
                if not str(d.resolve()).startswith(str(sessions_root.resolve())):
                    logger.warning("Refusing to delete path outside sessions: %s", d)
                    continue
                try:
                    shutil.rmtree(d)
                    logger.debug("Removed old scratchpad: %s", d)
                except OSError as exc:
                    logger.warning("Failed to remove scratchpad %s: %s", d, exc)


# ── 记忆索引加载 ───────────────────────────────────────────────────────────────


def load_memory_index(
    memory_path: str | None = None,
    cwd: str | None = None,
) -> str | None:
    """将 MEMORY.md 索引作为格式化段落加载。

    在项目记忆目录（``.pivot/memory/``）中查找 ``MEMORY.md``，
    或使用显式传入的 *memory_path*。
    会截断到 :data:`MAX_ENTRYPOINT_LINES` / :data:`MAX_ENTRYPOINT_BYTES`。

    返回 ``"## Your memory index ({ENTRYPOINT_NAME})\\n\\n{content}"``，
    如果没有记忆文件或文件为空则返回 ``None``。
    """
    if memory_path is not None:
        target = Path(memory_path)
    else:
        mem_dir = get_memory_dir(cwd)
        target = mem_dir / MEMORY_MD

    if not target.is_file():
        return None

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Failed to read memory file %s: %s", target, exc)
        return None

    if not content.strip():
        return None

    content = truncate_content(content)
    return f"## Your project memory index ({MEMORY_MD})\n\n{content}"


def load_global_memory_index() -> str | None:
    """从 ``~/.pivot/memory/`` 加载全局 MEMORY.md 索引。

    如果没有全局记忆则返回格式化段落或 None。
    """
    target = get_global_memory_dir() / MEMORY_MD

    if not target.is_file():
        return None

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Failed to read global memory file %s: %s", target, exc)
        return None

    if not content.strip():
        return None

    content = truncate_content(content)
    return f"## Your global memory index (~/.pivot/{MEMORY_MD})\n\n{content}"
