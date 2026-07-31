"""技能的工具限制——基于 allowed-tools 过滤可用工具。

当某个技能在其 frontmatter 中指定了 ``allowed-tools`` 时，
在技能执行期间只应提供与之匹配的工具。
"""

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pivotcode.tools.base import Tool

logger = logging.getLogger(__name__)

# 匹配带参数的工具限制，例如 "Bash(git:*)"
_PATTERN_RE = re.compile(r"^(\w+)\((.+)\)$")

# 将 allowed-tools 中的友好名称映射到实际的工具类名
_TOOL_NAME_ALIASES: dict[str, set[str]] = {
    "Bash": {"Bash"},
    "Read": {"Read", "FileRead"},
    "Write": {"Write", "FileWrite"},
    "Edit": {"Edit", "FileEdit"},
    "Glob": {"Glob"},
    "Grep": {"Grep"},
    "WebFetch": {"WebFetch"},
    "AskUser": {"AskUser", "AskUserQuestion"},
    "Skill": {"Skill"},
    "GitCommit": {"GitCommit"},
}


def _matches_tool_name(tool_name: str, pattern_name: str) -> bool:
    """检查工具名是否与模式名匹配（含别名解析）。"""
    aliases = _TOOL_NAME_ALIASES.get(pattern_name)
    if aliases:
        return tool_name in aliases
    # 回退：直接进行名称匹配
    return tool_name == pattern_name


def filter_tools_for_skill(
    all_tools: list["Tool"],
    allowed_patterns: list[str],
) -> list["Tool"]:
    """将工具过滤为仅与 allowed-tools 模式匹配的那些。

    模式为简单的工具名匹配：
    - ``"Bash"`` —— 允许 BashTool
    - ``"Read"`` —— 允许 FileReadTool
    - ``"Edit"`` —— 允许 FileEditTool
    - 等等。

    形如 ``Bash(git:*)`` 的基于模式的限制会被解析——只有
    工具名部分用于过滤；参数限制会被记录但不会在工具层面强制实施。

    Skill 工具始终被包含，以便模型可以调用其他技能。
    """
    if not allowed_patterns:
        return list(all_tools)

    # 将模式解析为纯工具名
    allowed_names: set[str] = set()
    for pattern in allowed_patterns:
        m = _PATTERN_RE.match(pattern)
        if m:
            tool_name = m.group(1)
            restriction = m.group(2)
            logger.debug(
                "Tool pattern %r: allowing %s with restriction %r (restriction logged, not enforced)",
                pattern, tool_name, restriction,
            )
            allowed_names.add(tool_name)
        else:
            allowed_names.add(pattern)

    # 始终包含 Skill 工具
    allowed_names.add("Skill")

    filtered = []
    for tool in all_tools:
        for allowed in allowed_names:
            if _matches_tool_name(tool.name, allowed):
                filtered.append(tool)
                break

    return filtered
