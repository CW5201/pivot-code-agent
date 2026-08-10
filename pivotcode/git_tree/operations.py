"""AGT 移动操作 —— move、revert、conv_revert、all_revert。

所有操作都以 ``agt_move`` 作为核心原语。
revert 是 move 的一种特殊情况（向后回退 n 个提交）。
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pivotcode.git_tree.memory_snapshots import (
    get_memory_diff,
    restore_memory_snapshot,
    take_memory_snapshot,
)

if TYPE_CHECKING:
    from pivotcode.session.state import SessionState

logger = logging.getLogger(__name__)


@dataclass
class MoveResult:
    """移动操作的结果。"""
    success: bool
    description: str
    old_sha: str = ""
    new_sha: str = ""
    repo_diff: str = ""
    memory_diff: str = ""
    new_branch: str = ""


@dataclass
class ConvRevertResult:
    """会话回退的结果。"""
    success: bool
    description: str
    steps_reverted: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# 核心操作
# ═══════════════════════════════════════════════════════════════════════════════


def agt_move(cwd: str, state: SessionState, target_sha: str) -> MoveResult:
    """将 agent 移动到目标提交。

    这是唯一的核心操作。所有其他移动操作
    都委托给本函数。

    步骤：
    1. 校验目标提交在 git 中存在
    2. 为当前内存创建快照（若当前位置是 pivot 提交）
    3. 丢弃未提交的改动
    4. 检出目标（必要时创建分支）
    5. 恢复目标对应的内存快照
    6. 更新会话状态
    7. 计算差异以供 agent 通知使用
    """
    old_sha = state.agent_position_sha or _git_head(cwd) or ""

    # 校验目标
    if not _sha_exists(cwd, target_sha):
        return MoveResult(False, f"Commit {target_sha[:7]} not found in git.")

    # 移动前为当前内存创建快照
    if old_sha:
        take_memory_snapshot(cwd, old_sha)

    # 丢弃未提交的改动（保留 .pivot/ 目录）。
    # 任一步骤失败都绝不能继续 —— 否则会话状态会记录一次
    # 并未真正发生的移动，agent 对现实的认知也会与
    # 工作树产生偏差。
    r = _run_git(cwd, "checkout", "-f")
    if r.returncode != 0:
        return MoveResult(False, f"git checkout -f failed: {r.stderr.strip()}")
    r = _run_git(cwd, "clean", "-fd", "-e", ".pivot")
    if r.returncode != 0:
        return MoveResult(False, f"git clean failed: {r.stderr.strip()}")

    # 决定检出策略
    target_branches = _branches_at(cwd, target_sha)
    current_branch = _git_current_branch(cwd)
    new_branch = ""

    if target_branches:
        # 目标是分支顶端 —— 直接检出该分支
        branch = target_branches[0]
        result = _run_git(cwd, "checkout", branch)
        if result.returncode != 0:
            return MoveResult(False, f"Failed to checkout {branch}: {result.stderr.strip()}")
    elif target_sha == _git_head(cwd):
        pass  # 已在目标位置
    else:
        # 需要新建一个分支
        new_branch = _unique_branch_name(cwd, target_sha)
        result = _run_git(cwd, "checkout", "-b", new_branch, target_sha)
        if result.returncode != 0:
            return MoveResult(False, f"Failed to create branch: {result.stderr.strip()}")

    # 恢复内存快照
    restore_memory_snapshot(cwd, target_sha)

    # 计算差异
    repo_diff = ""
    if old_sha and old_sha != target_sha:
        diff_result = _run_git(cwd, "diff", "--stat", old_sha, target_sha)
        if diff_result.returncode == 0:
            repo_diff = diff_result.stdout.strip()

    memory_diff = ""
    if old_sha:
        memory_diff = get_memory_diff(cwd, old_sha, target_sha)

    # 更新会话状态
    with state.batch():
        state.agent_position_sha = target_sha
        state.add_to_conv_path(target_sha)

    actual_branch = _git_current_branch(cwd) or new_branch or "detached"
    desc_parts = [f"Moved to {target_sha[:7]} on {actual_branch}"]
    if new_branch:
        desc_parts.append(f"(new branch: {new_branch})")
    if repo_diff:
        desc_parts.append(f"\nFiles changed:\n{repo_diff}")
    if memory_diff:
        desc_parts.append(f"\nMemory changes:\n{memory_diff}")

    return MoveResult(
        success=True,
        description="\n".join(desc_parts),
        old_sha=old_sha,
        new_sha=target_sha,
        repo_diff=repo_diff,
        memory_diff=memory_diff,
        new_branch=new_branch,
    )


def agt_revert(cwd: str, state: SessionState, n: int = 1) -> MoveResult:
    """从当前分支破坏性回退 n 个提交。

    使用 ``git reset --hard HEAD~n`` —— 这些提交会从分支上被**移除**。
    它们在约 30 天内仍可通过 ``git reflog`` 恢复。

    若存在未提交的改动且 n=1，则仅丢弃这些改动。
    """
    current = state.agent_position_sha or _git_head(cwd)
    if not current:
        return MoveResult(False, "No current position to revert from.")

    # 若脏且 n=1，仅丢弃未提交的改动
    is_dirty = bool(_run_git(cwd, "status", "--porcelain").stdout.strip())
    if is_dirty and n == 1:
        r = _run_git(cwd, "checkout", "-f")
        if r.returncode != 0:
            return MoveResult(False, f"git checkout -f failed: {r.stderr.strip()}")
        r = _run_git(cwd, "clean", "-fd", "-e", ".pivot")
        if r.returncode != 0:
            return MoveResult(False, f"git clean failed: {r.stderr.strip()}")
        return MoveResult(
            success=True,
            description=f"Discarded uncommitted changes. Still at {current[:7]}.",
            old_sha=current,
            new_sha=current,
        )

    # 计算有效步数（脏状态计为 1 步）
    effective_n = n if not is_dirty else n - 1
    if effective_n <= 0:
        effective_n = 1

    # 查找目标
    target_result = _run_git(cwd, "rev-parse", f"{current}~{effective_n}")
    if target_result.returncode != 0:
        actual_n = 0
        for i in range(effective_n - 1, 0, -1):
            result = _run_git(cwd, "rev-parse", f"{current}~{i}")
            if result.returncode == 0:
                actual_n = i
                break
        if actual_n == 0:
            return MoveResult(False, f"Cannot revert {n} commits from {current[:7]}.")
        target_sha = _run_git(cwd, "rev-parse", f"{current}~{actual_n}").stdout.strip()
    else:
        target_sha = target_result.stdout.strip()

    return _destructive_reset(cwd, state, target_sha)


def agt_revert_to(cwd: str, state: SessionState, target_sha: str) -> MoveResult:
    """破坏性回退到指定的提交 SHA。

    当前分支上 HEAD 与 *target_sha* 之间的所有提交
    都会通过 ``git reset --hard`` 被移除。目标必须是
    HEAD 的祖先。
    """
    current = state.agent_position_sha or _git_head(cwd)
    if not current:
        return MoveResult(False, "No current position to revert from.")

    if not _sha_exists(cwd, target_sha):
        return MoveResult(False, f"Commit {target_sha[:7]} not found.")

    # 校验目标是 current 的祖先（否则 reset 会出错）
    ancestor_check = _run_git(cwd, "merge-base", "--is-ancestor", target_sha, current)
    if ancestor_check.returncode != 0:
        return MoveResult(
            False,
            f"{target_sha[:7]} is not an ancestor of current HEAD. "
            f"Use /move instead for cross-branch navigation.",
        )

    if target_sha == current:
        # 已在目标位置 —— 若有脏状态则仅丢弃
        is_dirty = bool(_run_git(cwd, "status", "--porcelain").stdout.strip())
        if is_dirty:
            r = _run_git(cwd, "checkout", "-f")
            if r.returncode != 0:
                return MoveResult(False, f"git checkout -f failed: {r.stderr.strip()}")
            r = _run_git(cwd, "clean", "-fd", "-e", ".pivot")
            if r.returncode != 0:
                return MoveResult(False, f"git clean failed: {r.stderr.strip()}")
            return MoveResult(True, f"Discarded uncommitted changes. Still at {current[:7]}.",
                              old_sha=current, new_sha=current)
        return MoveResult(True, f"Already at {current[:7]}.", old_sha=current, new_sha=current)

    return _destructive_reset(cwd, state, target_sha)


def _destructive_reset(cwd: str, state: SessionState, target_sha: str) -> MoveResult:
    """核心破坏性重置：git reset --hard <target>，并更新状态。"""
    old_sha = state.agent_position_sha or _git_head(cwd) or ""

    # 回退前为内存创建快照
    if old_sha:
        take_memory_snapshot(cwd, old_sha)

    # 在销毁提交前计算差异
    diff_result = _run_git(cwd, "diff", "--stat", target_sha, old_sha)
    repo_diff = diff_result.stdout.strip() if diff_result.returncode == 0 else ""
    memory_diff = get_memory_diff(cwd, old_sha, target_sha) if old_sha else ""

    # 破坏性重置
    reset_result = _run_git(cwd, "reset", "--hard", target_sha)
    if reset_result.returncode != 0:
        return MoveResult(False, f"git reset --hard failed: {reset_result.stderr.strip()}")

    # 恢复内存快照
    restore_memory_snapshot(cwd, target_sha)

    # 查找被销毁的 SHA（old 与 target 之间的全部提交）
    destroyed_shas: set[str] = set()
    if old_sha and old_sha != target_sha:
        walk = old_sha
        for _ in range(200):  # 安全上限
            if walk == target_sha:
                break
            destroyed_shas.add(walk)
            parent = _run_git(cwd, "rev-parse", f"{walk}~1")
            if parent.returncode != 0:
                break
            walk = parent.stdout.strip()

    # 更新会话状态
    with state.batch():
        state.agent_position_sha = target_sha
        state.add_to_conv_path(target_sha)
        if destroyed_shas:
            state.pivot_commits = [s for s in state.pivot_commits if s not in destroyed_shas]

    branch = _git_current_branch(cwd) or "detached"
    n_destroyed = len(destroyed_shas)
    desc_parts = [f"Reverted {n_destroyed} commit(s). Now at {target_sha[:7]} on {branch}."]
    if repo_diff:
        desc_parts.append(f"\nFiles changed:\n{repo_diff}")
    if memory_diff:
        desc_parts.append(f"\nMemory changes:\n{memory_diff}")

    return MoveResult(
        success=True,
        description="\n".join(desc_parts),
        old_sha=old_sha,
        new_sha=target_sha,
        repo_diff=repo_diff,
        memory_diff=memory_diff,
    )


def agt_conv_revert(cwd: str, state: SessionState, n: int = 1) -> ConvRevertResult:
    """在会话路径上回退 n 步。

    这会截断会话路径，但**不会**移动 git 位置。
    agent 的位置保持不变。
    """
    conv = state.conv_path
    if len(conv) <= 1:
        return ConvRevertResult(False, "Conversation path is too short to revert.", 0)

    actual_n = min(n, len(conv) - 1)  # Keep at least 1 entry
    new_conv = conv[:-actual_n]

    # 检查是否跨越了压缩标记
    markers = state.compaction_markers
    new_markers = [m for m in markers if m in set(new_conv)]

    with state.batch():
        state.conv_path = new_conv
        if len(new_markers) != len(markers):
            state.compaction_markers = new_markers

    return ConvRevertResult(
        success=True,
        description=f"Conversation reverted {actual_n} step(s). Position unchanged.",
        steps_reverted=actual_n,
    )


def agt_all_revert(cwd: str, state: SessionState, n: int = 1) -> MoveResult:
    """将 git 位置与会话路径同时回退 n 步。"""
    # 首先，回退会话
    conv_result = agt_conv_revert(cwd, state, n)

    # 然后，回退 git 位置
    move_result = agt_revert(cwd, state, n)

    if move_result.success:
        move_result.description = (
            f"All-reverted {n} step(s).\n"
            f"Conversation: {conv_result.description}\n"
            f"Position: {move_result.description}"
        )
    return move_result


def detect_orphaned_shas(cwd: str, state: SessionState) -> list[str]:
    """找出会话状态中已无法从任何分支到达的 SHA。

    使用 ``git branch --contains`` 检查可达性，而不仅仅是对象是否存在
    （在 ``git reset --hard`` 之后，不可达对象仍会存在于 git 的对象库中）。

    返回孤立 SHA 列表，并从状态中清理掉它们。
    """
    orphaned: list[str] = []

    all_shas = set(state.pivot_commits) | set(state.conv_path) | set(state.compaction_markers)
    if state.session_root_sha:
        all_shas.add(state.session_root_sha)
    if state.agent_position_sha:
        all_shas.add(state.agent_position_sha)

    for sha in all_shas:
        if not _sha_reachable(cwd, sha):
            orphaned.append(sha)

    if orphaned:
        orphaned_set = set(orphaned)
        with state.batch():
            state.pivot_commits = [s for s in state.pivot_commits if s not in orphaned_set]
            state.conv_path = [s for s in state.conv_path if s not in orphaned_set]
            state.compaction_markers = [s for s in state.compaction_markers if s not in orphaned_set]
            if state.session_root_sha in orphaned_set:
                state.session_root_sha = _git_head(cwd) or ""
            if state.agent_position_sha in orphaned_set:
                state.agent_position_sha = _git_head(cwd) or ""
        logger.info("Cleaned %d orphaned SHAs from session state", len(orphaned))

    return orphaned


# ═══════════════════════════════════════════════════════════════════════════════
# Git 辅助函数
# ═══════════════════════════════════════════════════════════════════════════════


def _run_git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    """执行 git 命令。"""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def _git_head(cwd: str) -> str | None:
    result = _run_git(cwd, "rev-parse", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else None


def _git_current_branch(cwd: str) -> str | None:
    result = _run_git(cwd, "symbolic-ref", "--short", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else None


def _sha_exists(cwd: str, sha: str) -> bool:
    result = _run_git(cwd, "cat-file", "-t", sha)
    return result.returncode == 0


def _sha_reachable(cwd: str, sha: str) -> bool:
    """检查某个 SHA 是否能从任意分支到达（而不只是作为对象存在）。"""
    result = _run_git(cwd, "branch", "--all", "--contains", sha)
    return result.returncode == 0 and bool(result.stdout.strip())


def _branches_at(cwd: str, sha: str) -> list[str]:
    """返回末端恰好位于该 SHA 的分支名。"""
    result = _run_git(cwd, "branch", "--points-at", sha, "--format=%(refname:short)")
    if result.returncode != 0:
        return []
    return [b.strip() for b in result.stdout.strip().split("\n") if b.strip()]


def _unique_branch_name(cwd: str, sha: str) -> str:
    """生成一个唯一的分子名，形如 pivot/move-abc1234。"""
    short = sha[:7]
    base = f"pivot/move-{short}"
    name = base
    counter = 2
    while True:
        result = _run_git(cwd, "branch", "--list", name)
        if not result.stdout.strip():
            return name
        name = f"{base}-{counter}"
        counter += 1
