"""AGT 数据模型 —— 节点、边与树形结构。

所有数据都派生自 ``git log``。树形结构从不单独存储，
而是在每次更新时从 git 重新解析。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    """AGT 中节点的类型。"""
    PIVOT_COMMIT = "pivot_commit"       # 蓝色 —— 通过 GitCommit 工具提交的节点
    EXTERNAL_COMMIT = "external"       # 灰色 —— 其他任意提交
    CURRENT_NODE = "current"           # 白色虚线 —— 未提交的改动


# "current 节点"（工作树脏状态）对应的虚拟 SHA
CURRENT_NODE_SHA = "__dirty__"


@dataclass
class AGTNode:
    """Agentic Git Tree 中的单个节点。"""
    sha: str
    short_sha: str
    message: str
    author: str
    timestamp: str                     # ISO 8601 格式
    parents: list[str]                 # 父提交 SHA 列表
    children: list[str] = field(default_factory=list)
    node_type: NodeType = NodeType.EXTERNAL_COMMIT
    branches: list[str] = field(default_factory=list)  # 此提交所在的分支名
    is_head: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "short_sha": self.short_sha,
            "message": self.message,
            "author": self.author,
            "timestamp": self.timestamp,
            "parents": self.parents,
            "children": self.children,
            "node_type": self.node_type.value,
            "branches": self.branches,
            "is_head": self.is_head,
        }


@dataclass
class AGTTree:
    """从 git log 派生的完整 Agentic Git Tree。

    Attributes
    ----------
    nodes : dict[str, AGTNode]
        SHA → 节点的映射。若工作树处于脏状态，会包含虚拟的 ``CURRENT_NODE_SHA``。
    root_shas : list[str]
        没有父提交的提交（初始提交）。
    head_sha : str | None
        HEAD 所指向的提交（即使处于脏状态也是如此）。
    is_dirty : bool
        若工作树存在未提交的改动则为 True。
    current_branch : str | None
        当前分支名；若为分离 HEAD 则为 None。
    """

    nodes: dict[str, AGTNode] = field(default_factory=dict)
    root_shas: list[str] = field(default_factory=list)
    head_sha: str | None = None
    is_dirty: bool = False
    current_branch: str | None = None

    # ── 访问方法────────────────────────────────────────────────────

    def get_node(self, sha: str) -> AGTNode | None:
        """根据 SHA 返回节点，找不到则返回 None。"""
        return self.nodes.get(sha)

    @property
    def commit_count(self) -> int:
        """真实提交的数量（不含虚拟的 current 节点）。"""
        return sum(
            1 for n in self.nodes.values()
            if n.node_type != NodeType.CURRENT_NODE
        )

    def walk_ancestors(self, sha: str, n: int) -> list[str]:
        """沿第一父节点链向前回溯 n 个祖先。

        返回一个 SHA 列表，从直接父提交开始，
        最多回溯 n 个祖先。若到达根节点则提前停止。
        """
        result: list[str] = []
        current = sha
        for _ in range(n):
            node = self.nodes.get(current)
            if not node or not node.parents:
                break
            parent = node.parents[0]  # First parent
            result.append(parent)
            current = parent
        return result

    def get_mainline(self) -> list[str]:
        """返回从 HEAD 到根节点的第一父节点链。

        这是开发的"主线"——树的骨干。
        """
        if not self.head_sha:
            return []
        chain: list[str] = [self.head_sha]
        current = self.head_sha
        while True:
            node = self.nodes.get(current)
            if not node or not node.parents:
                break
            current = node.parents[0]
            chain.append(current)
        return chain

    def sha_exists(self, sha: str) -> bool:
        """检查某个 SHA 是否存在于树中。"""
        return sha in self.nodes

    # ── 序列化───────────────────────────────────────────────────

    def to_json(self) -> dict[str, Any]:
        """将树序列化为兼容 JSON 的字典。"""
        return {
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "root_shas": self.root_shas,
            "head_sha": self.head_sha,
            "is_dirty": self.is_dirty,
            "current_branch": self.current_branch,
            "commit_count": self.commit_count,
        }
