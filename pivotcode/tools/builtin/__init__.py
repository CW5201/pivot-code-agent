"""Pivot Code的内置工具。"""

from pivotcode.tools.builtin.ask_user import AskUserQuestionTool
from pivotcode.tools.builtin.bash import BashTool
from pivotcode.tools.builtin.file_edit import FileEditTool
from pivotcode.tools.builtin.file_read import FileReadTool
from pivotcode.tools.builtin.file_write import FileWriteTool
from pivotcode.tools.builtin.git_commit import GitCommitTool
from pivotcode.tools.builtin.glob_tool import GlobTool
from pivotcode.tools.builtin.grep_tool import GrepTool
from pivotcode.tools.builtin.web_fetch import WebFetchTool
from pivotcode.tools.builtin.web_search import WebSearchTool

ALL_BUILTIN_TOOLS = [
    BashTool(),
    FileReadTool(),
    FileWriteTool(),
    FileEditTool(),
    GlobTool(),
    GrepTool(),
    WebFetchTool(),
    WebSearchTool(),
    AskUserQuestionTool(),
    GitCommitTool(),
]

__all__ = [
    "ALL_BUILTIN_TOOLS",
    "AskUserQuestionTool",
    "BashTool",
    "FileEditTool",
    "FileReadTool",
    "FileWriteTool",
    "GlobTool",
    "GrepTool",
    "WebFetchTool",
    "WebSearchTool",
]
