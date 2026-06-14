"""工具注册表 - 管理可用工具池。"""

from pivotcode.tools.base import Tool
from pivotcode.tools.builtin import ALL_BUILTIN_TOOLS

# 默认在编程模式下排除的工具：外部网络
# （WebFetch）、git变更（GitCommit）和用户提示（AskUser）。
PROGRAMMATIC_EXCLUDED_TOOL_NAMES = frozenset({
    "WebFetch", "GitCommit", "AskUserQuestion",
})


def get_all_builtin_tools() -> list[Tool]:
    """返回所有内置工具。"""
    return list(ALL_BUILTIN_TOOLS)


def get_enabled_tools(tools: list[Tool] | None = None) -> list[Tool]:
    """仅过滤启用的工具。"""
    all_tools = tools or get_all_builtin_tools()
    return [t for t in all_tools if t.is_enabled()]


def get_programmatic_tool_set() -> list[Tool]:
    """返回编程模式下默认使用的工具集。"""
    return [
        t for t in get_enabled_tools()
        if t.name not in PROGRAMMATIC_EXCLUDED_TOOL_NAMES
    ]


def find_tool_by_name(tools: list[Tool], name: str) -> Tool | None:
    """通过名称或别名查找工具。"""
    for t in tools:
        if t.matches_name(name):
            return t
    return None


def tools_to_schemas(tools: list[Tool]) -> list[dict]:
    """将工具转换为API模式格式。"""
    return [t.to_schema() for t in tools]
