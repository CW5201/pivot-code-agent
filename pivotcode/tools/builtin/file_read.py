"""FileReadTool - 读取文件内容并显示行号。"""

import os
from typing import Any

from pivotcode.tools.base import Tool, ToolResult, ToolUseContext


class FileReadTool(Tool):
    """读取文件内容并显示行号（cat -n格式）。"""

    @property
    def name(self) -> str:
        return "Read"

    @property
    def description(self) -> str:
        return (
            "从本地文件系统读取文件。您可以直接使用此工具访问任何文件。"
            "假设此工具能够读取机器上的所有文件。\n\n"
            "用法:\n"
            "- file_path参数必须是绝对路径，不是相对路径\n"
            "- 默认情况下，从文件开头读取最多2000行\n"
            "- 当您已经知道需要文件的哪一部分时，只读取那部分\n"
            "- 结果使用cat -n格式返回，行号从1开始\n"
            "- 此工具只能读取文件，不能读取目录。要读取目录，"
            "请使用Bash工具和'ls'。\n"
            "- 如果您读取的文件存在但内容为空，您将收到警告而不是文件内容。"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要读取的文件的绝对路径（必须是绝对路径，不是相对路径）。",
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "开始读取的行号（从0开始）。 "
                        "仅在文件太大无法一次读取时提供。"
                    ),
                    "minimum": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "要读取的行数。默认2000。 "
                        "仅在文件太大无法一次读取时提供。"
                    ),
                    "exclusiveMinimum": 0,
                },
            },
            "required": ["file_path"],
        }

    def permission_level(self, args: dict[str, Any]) -> str:
        return "read"

    async def call(self, args: dict[str, Any], context: ToolUseContext) -> ToolResult:
        file_path = args.get("file_path", "")
        offset = args.get("offset", 0)
        limit = args.get("limit", 2000)

        if not file_path:
            given_keys = list(args.keys())
            return ToolResult(
                data=f"错误：'file_path'参数是必需的但未提供。 "
                     f"收到的参数：{given_keys}。 "
                     f"使用<arg_key>file_path</arg_key><arg_value>/绝对路径/到/文件</arg_value>",
                is_error=True,
            )

        # 解析相对路径并跟踪符号链接
        if not os.path.isabs(file_path):
            file_path = os.path.join(context.cwd, file_path)
        file_path = os.path.realpath(file_path)

        if not os.path.exists(file_path):
            return ToolResult(data=f"错误：找不到文件：{file_path}", is_error=True)

        if os.path.isdir(file_path):
            return ToolResult(
                data=f"错误：{file_path}是目录，不是文件。请使用Bash和'ls'列出目录。",
                is_error=True,
            )

        # 检查二进制文件
        try:
            with open(file_path, "rb") as f:
                chunk = f.read(8192)
            if b"\x00" in chunk:
                return ToolResult(
                    data=f"错误：{file_path}似乎是二进制文件。",
                    is_error=True,
                )
        except PermissionError:
            return ToolResult(data=f"错误：权限被拒绝读取{file_path}", is_error=True)
        except Exception as exc:
            return ToolResult(data=f"Error reading file: {exc}", is_error=True)

        # 读取文件
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except Exception as exc:
            return ToolResult(data=f"读取文件时出错：{exc}", is_error=True)

        total_lines = len(all_lines)
        if total_lines == 0:
            return ToolResult(data=f"(empty file: {file_path})")

        if offset >= total_lines:
            return ToolResult(
                data=f"Warning: offset {offset} exceeds file length ({total_lines} lines).",
                is_error=True,
            )

        selected = all_lines[offset : offset + limit]

        # 格式化行号（从1开始，匹配cat -n）
        result_lines = []
        for i, line in enumerate(selected, start=offset + 1):
            # 右对齐行号占6个字符，制表符，然后是内容（无尾部换行符）
            result_lines.append(f"{i:>6}\t{line.rstrip()}")

        output = "\n".join(result_lines)

        # 如果截断则警告
        remaining = total_lines - (offset + len(selected))
        if remaining > 0:
            output += f"\n\n... ({remaining} more lines not shown. Use offset={offset + limit} to continue.)"

        return ToolResult(data=output)
