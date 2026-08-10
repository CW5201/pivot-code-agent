"""将 ``git log --all`` 解析为 :class:`AGTTree`。

这是构建树的唯一入口。它会执行 git 命令，
解析其输出，并返回一个已完整填充的树。
树从不缓存 —— 每次调用都会重新解析。
"""

from __future__ import annotations

import logging
import os
import subprocess

from pivotcode.git_tree.model import CURRENT_NODE_SHA, AGTNode, AGTTree, NodeType

logger = logging.getLogger(__name__)

# git log 格式字符串中使用的分隔符（提交信息里很少出现竖线，
# 但为安全起见我们使用带填充的双竖线）
_SEP = " ||| "
_FORMAT = f"%H{_SEP}%P{_SEP}%s{_SEP}%an{_SEP}%aI{_SEP}%D"


def parse_git_tree(
    cwd: str,
    pivot_commits: set[str] | None = None,
) -> AGTTree:
    """将位于 *cwd* 的 git 仓库解析为 :class:`AGTTree`。

    Parameters
    ----------
    cwd : str
        git 仓库的路径。
    pivot_commits : set[str], optional
        由 agent（通过 GitCommit 工具）创建的提交 SHA 集合。
        这些会被归类为 ``PIVOT_COMMIT``，其余均归类为 ``EXTERNAL_COMMIT``。

    Returns
    -------
    AGTTree
        解析后的树。若 *cwd* 不是 git 仓库或没有提交，
        则返回一个空树（若已存在未跟踪/已修改的文件，
        可能会附带一个虚拟的 current 节点）。
    """
    pivot_commits = pivot_commits or set()

    # ── 获取 HEAD 信息───────────────────────────────────────────────
    head_sha = _git_head_sha(cwd)
    current_branch = _git_current_branch(cwd)
    is_dirty = _git_is_dirty(cwd)

    # ── 解析 git log───────────────────────────────────────────────
    nodes: dict[str, AGTNode] = {}
    root_shas: list[str] = []

    log_output = _git_log_all(cwd)
    if log_output:
        for line in log_output.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            node = _parse_log_line(line, pivot_commits, head_sha)
            if node:
                nodes[node.sha] = node
                if not node.parents:
                    root_shas.append(node.sha)

    # ── 填充子节点（parents 的逆关系）───────────────────────
    for node in nodes.values():
        for parent_sha in node.parents:
            parent = nodes.get(parent_sha)
            if parent and node.sha not in parent.children:
                parent.children.append(node.sha)

    # ── 若工作树脏则添加虚拟 current 节点────────────────────────────
    if is_dirty:
        current_node = AGTNode(
            sha=CURRENT_NODE_SHA,
            short_sha="dirty",
            message="Uncommitted changes",
            author="",
            timestamp="",
            parents=[head_sha] if head_sha else [],
            children=[],
            node_type=NodeType.CURRENT_NODE,
            branches=[],
            is_head=False,
        )
        nodes[CURRENT_NODE_SHA] = current_node
        # 作为 HEAD 的子节点添加
        if head_sha and head_sha in nodes:
            nodes[head_sha].children.append(CURRENT_NODE_SHA)

    return AGTTree(
        nodes=nodes,
        root_shas=root_shas,
        head_sha=head_sha,
        is_dirty=is_dirty,
        current_branch=current_branch,
    )


# ── Git 命令─────────────────────────────────────────────────────────────────


def _run_git(cwd: str, *args: str) -> str | None:
    """执行 git 命令并返回 stdout，失败则返回 None。"""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if result.returncode == 0:
            return result.stdout
        return None
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None


def _git_head_sha(cwd: str) -> str | None:
    """返回 HEAD 的完整 SHA，失败则返回 None。"""
    out = _run_git(cwd, "rev-parse", "HEAD")
    return out.strip() if out else None


def _git_current_branch(cwd: str) -> str | None:
    """返回当前分支名；若为分离 HEAD 则返回 None。"""
    out = _run_git(cwd, "symbolic-ref", "--short", "HEAD")
    return out.strip() if out else None


def _git_is_dirty(cwd: str) -> bool:
    """检查工作树是否存在未提交的改动。"""
    out = _run_git(cwd, "status", "--porcelain")
    return bool(out and out.strip())


def _git_log_all(cwd: str) -> str | None:
    """以自定义格式运行 git log --all。"""
    return _run_git(
        cwd, "log", "--all",
        f"--format={_FORMAT}",
        "--topo-order",
    )


# ── 行解析─────────────────────────────────────────────────────────────


def _parse_log_line(
    line: str,
    pivot_commits: set[str],
    head_sha: str | None,
) -> AGTNode | None:
    """将单行 git log 解析为一个 AGTNode。"""
    parts = line.split(_SEP)
    # 至少需要 5 个部分（当 git 在末尾输出分隔符且其后没有空格时，
    # decorations 可能为空或缺失）
    if len(parts) < 5:
        logger.debug("Skipping malformed git log line: %s", line[:80])
        return None

    sha = parts[0].strip()
    parent_str = parts[1].strip()
    message = parts[2].strip()
    author = parts[3].strip()
    timestamp = parts[4].strip()
    decorations = parts[5].strip() if len(parts) > 5 else ""

    parents = parent_str.split() if parent_str else []

    # 从诸如 "HEAD -> main, origin/main" 的装饰信息中解析分支名
    branches: list[str] = []
    if decorations:
        for dec in decorations.split(","):
            dec = dec.strip()
            if dec.startswith("HEAD -> "):
                branches.append(dec[8:])
            elif dec == "HEAD":
                continue  # 分离 HEAD，没有分支名
            elif "/" in dec:
                continue  # 跳过诸如 origin/main 之类的远程引用
            else:
                branches.append(dec)

    node_type = (
        NodeType.PIVOT_COMMIT if sha in pivot_commits
        else NodeType.EXTERNAL_COMMIT
    )

    return AGTNode(
        sha=sha,
        short_sha=sha[:7],
        message=message,
        author=author,
        timestamp=timestamp,
        parents=parents,
        children=[],
        node_type=node_type,
        branches=branches,
        is_head=(sha == head_sha),
    )
