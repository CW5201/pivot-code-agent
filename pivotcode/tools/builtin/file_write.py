"""FileWriteTool - 创建或覆盖文件。"""

import difflib
import os
from typing import Any

from pivotcode.tools.base import Tool, ToolResult, ToolUseContext


class FileWriteTool(Tool):
    """在本地文件系统上创建或覆盖文件。"""

    @property
    def name(self) -> str:
        return "Write"

    @property
    def description(self) -> str:
        return (
            "将文件写入本地文件系统。\n\n"
            "用法:\n"
            "- 如果提供的路径中存在现有文件，此工具将覆盖它。\n"
            "- 如果这是现有文件，您必须首先使用Read工具读取文件内容。"
            "如果您未先读取文件，此工具将失败。\n"
            "- 对于修改现有文件，优先使用Edit工具——它只发送差异。"
            "仅使用此工具创建新文件或进行完全重写。\n"
            "- 除非用户明确要求，否则不要创建文档文件（*.md）或README文件。"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要写入的文件的绝对路径（必须是绝对路径，不是相对路径）。",
                },
                "content": {
                    "type": "string",
                    "description": "要写入文件的内容。",
                },
            },
            "required": ["file_path", "content"],
        }

    def permission_level(self, args: dict[str, Any]) -> str:
        return "write"

    async def call(self, args: dict[str, Any], context: ToolUseContext) -> ToolResult:
        file_path = args.get("file_path", "")
        content = args.get("content", "")

        if not file_path:
            given_keys = list(args.keys())
            return ToolResult(
                data=f"错误：'file_path'参数是必需的但未提供。 "
                     f"收到的参数：{given_keys}。 "
                     f"使用<arg_key>file_path</arg_key><arg_value>/绝对路径/到/文件</arg_value> "
                     f"和<arg_key>content</arg_key><arg_value>文件内容</arg_value>",
                is_error=True,
            )

        # 解析相对路径并跟踪符号链接
        if not os.path.isabs(file_path):
            file_path = os.path.join(context.cwd, file_path)
        file_path = os.path.realpath(file_path)

        # 如果需要，创建父目录
        parent = os.path.dirname(file_path)
        try:
            if parent:
                os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            return ToolResult(data=f"创建目录{parent}时出错：{exc}", is_error=True)

        existed = os.path.exists(file_path)
        old_content = ""
        if existed:
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    old_content = f.read()
            except Exception:
                old_content = ""

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except PermissionError:
            return ToolResult(data=f"错误：权限被拒绝写入{file_path}", is_error=True)
        except Exception as exc:
            return ToolResult(data=f"写入文件时出错：{exc}", is_error=True)

        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        verb = "Overwrote" if existed else "Created"
        summary = f"{verb} {file_path} ({line_count} lines, {len(content)} chars)"

        diff_text = _make_write_diff(old_content, content, file_path, existed)
        if diff_text:
            return ToolResult(data=f"[ALAN-DIFF]\n{diff_text}\n{summary}")
        return ToolResult(data=summary)


def _make_write_diff(
    old: str, new: str, path: str, existed: bool, context: int = 3,
) -> str:
    """返回写入操作的统一差异。

    - 覆盖时：旧内容与新内容的差异。
    - 创建时：从空开始的差异——整个文件显示为'+'行。
    """
    old_lines = old.splitlines(keepends=True) if existed else []
    new_lines = new.splitlines(keepends=True)
    diff_iter = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=path if existed else "/dev/null",
        tofile=path,
        n=context,
    )
    return "".join(diff_iter).rstrip("\n")
