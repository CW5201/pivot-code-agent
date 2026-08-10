"""树布局算法 —— 计算用于 SVG 渲染的 (x, y) 坐标。

Y 轴：根节点在顶部 (y=0)，最新的提交在底部。
X 轴：主线在 x=0，分支向左右两侧偏移。

主线由 ``main``（或 ``master``）分支确定，
而不是由 HEAD 决定 —— 这样切换到其他分支时布局不会重新排列。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from pivotcode.git_tree.model import CURRENT_NODE_SHA, AGTTree


@dataclass
class LayoutNode:
    """一个用于渲染的定位节点。"""
    sha: str
    x: float
    y: float
    node_type: str
    short_sha: str
    message: str
    author: str
    timestamp: str
    branches: list[str]
    is_head: bool
    is_compaction_marker: bool = False
    is_agent_position: bool = False
    is_on_conv_path: bool = False
    is_session_root: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "x": self.x,
            "y": self.y,
            "node_type": self.node_type,
            "short_sha": self.short_sha,
            "message": self.message,
            "author": self.author,
            "timestamp": self.timestamp,
            "branches": self.branches,
            "is_head": self.is_head,
            "is_compaction_marker": self.is_compaction_marker,
            "is_agent_position": self.is_agent_position,
            "is_on_conv_path": self.is_on_conv_path,
            "is_session_root": self.is_session_root,
        }


@dataclass
class LayoutEdge:
    """两个节点之间用于渲染的边。"""
    from_sha: str
    to_sha: str
    edge_type: str  # edge_type 取值："parent"（父节点）、"conv_path"（会话路径）、"post_compaction"（压缩后）、"conv_jump"（会话跳跃）

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_sha": self.from_sha,
            "to_sha": self.to_sha,
            "edge_type": self.edge_type,
        }


@dataclass
class TreeLayout:
    """已完成、可直接用于 SVG 渲染的完整布局。"""
    nodes: list[LayoutNode] = field(default_factory=list)
    edges: list[LayoutEdge] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "width": self.width,
            "height": self.height,
        }


def compute_layout(
    tree: AGTTree,
    conv_path: list[str] | None = None,
    compaction_markers: list[str] | None = None,
    agent_position: str | None = None,
    session_root: str | None = None,
) -> TreeLayout:
    """计算用于渲染的定位节点与分类后的边。"""
    if not tree.nodes:
        return TreeLayout()

    conv_path = conv_path or []
    compaction_markers = compaction_markers or []
    conv_path_set = set(conv_path)
    compaction_set = set(compaction_markers)

    # ── 步骤 1：拓扑排序（父节点在子节点之前）───────────
    sorted_shas = _topo_sort(tree)

    # ── 步骤 2：Y 坐标（行索引，最旧的在顶部）───────────────
    y_map: dict[str, float] = {}
    for i, sha in enumerate(sorted_shas):
        y_map[sha] = float(i)

    # ── 步骤 3：X 坐标（分支泳道）───────────────────────────
    mainline_set = _find_mainline(tree)
    x_map = _assign_x_positions(tree, sorted_shas, mainline_set)

    # ── 步骤 4：构建布局节点───────────────────────────────────
    layout_nodes: list[LayoutNode] = []
    for sha in sorted_shas:
        node = tree.get_node(sha)
        if not node:
            continue
        layout_nodes.append(LayoutNode(
            sha=sha,
            x=x_map.get(sha, 0.0),
            y=y_map.get(sha, 0.0),
            node_type=node.node_type.value,
            short_sha=node.short_sha,
            message=node.message,
            author=node.author,
            timestamp=node.timestamp,
            branches=node.branches,
            is_head=node.is_head,
            is_compaction_marker=(sha in compaction_set),
            is_agent_position=(sha == agent_position),
            is_on_conv_path=(sha in conv_path_set),
            is_session_root=(sha == session_root),
        ))

    # ── 步骤 5：构建边─────────────────────────────────────────
    layout_edges: list[LayoutEdge] = []

    # 父节点边
    for sha in sorted_shas:
        node = tree.get_node(sha)
        if not node:
            continue
        for parent_sha in node.parents:
            if parent_sha in tree.nodes:
                layout_edges.append(LayoutEdge(parent_sha, sha, "parent"))

    # 会话路径边（蓝色）
    for i in range(len(conv_path) - 1):
        src, dst = conv_path[i], conv_path[i + 1]
        if src not in tree.nodes or dst not in tree.nodes:
            continue
        dst_node = tree.get_node(dst)
        is_parent_child = dst_node and src in dst_node.parents
        layout_edges.append(LayoutEdge(
            src, dst, "conv_path" if is_parent_child else "conv_jump",
        ))

    # 压缩后边（黄色）—— 从最后一个压缩标记（或会话根）到 agent 当前位置。始终显示。
    if conv_path and agent_position:
        start_sha = conv_path[0]
        if compaction_markers:
            last_marker = compaction_markers[-1]
            if last_marker in conv_path_set:
                start_sha = last_marker
        try:
            start_idx = conv_path.index(start_sha)
            for i in range(start_idx, len(conv_path) - 1):
                src, dst = conv_path[i], conv_path[i + 1]
                if src in tree.nodes and dst in tree.nodes:
                    layout_edges.append(LayoutEdge(src, dst, "post_compaction"))
        except ValueError:
            pass

    # 会话间隔弧：若 agent_position 不是 conv_path 的最后一个元素，
    # 则从会话末端到 agent_position 绘制一条虚线弧。
    # 这种情况出现在 /convrevert 之后 —— 会话被截断，但 agent
    # 仍停留在更靠后的提交上。
    if conv_path and agent_position and agent_position in tree.nodes:
        last_conv = conv_path[-1]
        if last_conv != agent_position and last_conv in tree.nodes:
            layout_edges.append(LayoutEdge(last_conv, agent_position, "conv_jump"))
            layout_edges.append(LayoutEdge(last_conv, agent_position, "post_compaction"))

    # ── 步骤 6：计算边界───────────────────────────────────────
    if layout_nodes:
        min_x = min(n.x for n in layout_nodes)
        max_x = max(n.x for n in layout_nodes)
        max_y = max(n.y for n in layout_nodes)
        width = max_x - min_x + 2
        height = max_y + 1
    else:
        width = height = 0.0

    return TreeLayout(nodes=layout_nodes, edges=layout_edges,
                      width=width, height=height)


# ═══════════════════════════════════════════════════════════════════════════════
# 内部辅助函数
# ═══════════════════════════════════════════════════════════════════════════════


def _topo_sort(tree: AGTTree) -> list[str]:
    """拓扑排序：父节点在子节点之前，CURRENT_NODE 排在最后。"""
    in_degree: dict[str, int] = {sha: 0 for sha in tree.nodes}
    for sha, node in tree.nodes.items():
        if sha == CURRENT_NODE_SHA:
            continue
        for child_sha in node.children:
            if child_sha in in_degree and child_sha != CURRENT_NODE_SHA:
                in_degree[child_sha] += 1

    queue = deque(
        sha for sha, deg in in_degree.items()
        if deg == 0 and sha != CURRENT_NODE_SHA
    )
    result: list[str] = []
    while queue:
        sha = queue.popleft()
        result.append(sha)
        node = tree.nodes.get(sha)
        if not node:
            continue
        for child_sha in node.children:
            if child_sha == CURRENT_NODE_SHA or child_sha not in in_degree:
                continue
            in_degree[child_sha] -= 1
            if in_degree[child_sha] == 0:
                queue.append(child_sha)

    visited = set(result)
    for sha in tree.nodes:
        if sha not in visited and sha != CURRENT_NODE_SHA:
            result.append(sha)
    if CURRENT_NODE_SHA in tree.nodes:
        result.append(CURRENT_NODE_SHA)
    return result


def _find_mainline(tree: AGTTree) -> set[str]:
    """查找主线（从 main/master 分支顶端出发的第一父节点链）。

    如果存在 ``main`` 或 ``master`` 分支则使用它，否则回退到 HEAD。
    这样可以保证当用户切换到其他分支时，主线不会发生变化。
    """
    # 查找 main/master 分支的顶端
    main_sha = None
    for sha, node in tree.nodes.items():
        if sha == CURRENT_NODE_SHA:
            continue
        for branch in node.branches:
            if branch in ("main", "master"):
                main_sha = sha
                break
        if main_sha:
            break

    # 回退到 HEAD
    if not main_sha:
        main_sha = tree.head_sha

    if not main_sha:
        return set()

    # 沿第一父节点链向下遍历
    chain: set[str] = set()
    current = main_sha
    while current:
        chain.add(current)
        node = tree.nodes.get(current)
        if not node or not node.parents:
            break
        current = node.parents[0]
    return chain


def _assign_x_positions(
    tree: AGTTree,
    sorted_shas: list[str],
    mainline_set: set[str],
) -> dict[str, float]:
    """为节点分配水平泳道。

    主线 = x=0。每个非主线分支各自占用一条泳道，
    在左右两侧交替。与兄弟节点共用 x 坐标的子节点
    会被强制分配到新的泳道。
    """
    x_map: dict[str, float] = {}
    # 记录每个 y 层级上已被占用的 x 坐标，以避免重叠
    used_x_at_y: dict[float, set[float]] = {}
    branch_lanes: dict[str, float] = {}
    next_lane = 1
    lane_sign = 1  # 左右交替（+/-）

    y_map: dict[str, float] = {}
    for i, sha in enumerate(sorted_shas):
        y_map[sha] = float(i)

    for sha in sorted_shas:
        node = tree.get_node(sha)
        if not node:
            continue
        y = y_map.get(sha, 0.0)

        if sha == CURRENT_NODE_SHA:
            head_x = x_map.get(tree.head_sha, 0.0) if tree.head_sha else 0.0
            x_map[sha] = head_x
            continue

        if sha in mainline_set:
            x_map[sha] = 0.0
            used_x_at_y.setdefault(y, set()).add(0.0)
            continue

        # 非主线：尝试从同分支的父节点继承泳道
        assigned = False

        # 第一次尝试：从非主线的父节点继承
        for p in node.parents:
            if p in x_map and p not in mainline_set:
                candidate_x = x_map[p]
                if candidate_x not in used_x_at_y.get(y, set()):
                    x_map[sha] = candidate_x
                    used_x_at_y.setdefault(y, set()).add(candidate_x)
                    assigned = True
                    break

        if assigned:
            continue

        # 第二次尝试：使用分支名对应的泳道
        branch_name = node.branches[0] if node.branches else None
        if branch_name and branch_name in branch_lanes:
            candidate_x = branch_lanes[branch_name]
            if candidate_x not in used_x_at_y.get(y, set()):
                x_map[sha] = candidate_x
                used_x_at_y.setdefault(y, set()).add(candidate_x)
                continue

        # 需要分配新的泳道
        lane_x = next_lane * lane_sign
        # 确保该泳道在此 y 层级上未被占用
        while lane_x in used_x_at_y.get(y, set()):
            next_lane += 1
            lane_sign *= -1
            lane_x = next_lane * lane_sign

        x_map[sha] = lane_x
        used_x_at_y.setdefault(y, set()).add(lane_x)
        if branch_name:
            branch_lanes[branch_name] = lane_x
        next_lane += 1
        lane_sign *= -1

    return x_map
