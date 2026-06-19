"""FileEditTool - 在文件中执行字符串替换。"""

import difflib
import os
from typing import Any

from pivotcode.tools.base import Tool, ToolResult, ToolUseContext


class FileEditTool(Tool):
    """在文件中执行精确的字符串替换。"""

    @property
    def name(self) -> str:
        return "Edit"

    @property
    def description(self) -> str:
        return (
            "在文件中执行精确的字符串替换。\n\n"
            "用法:\n"
            "- 您必须在对话中至少使用一次Read工具 "
            "然后再编辑。如果您尝试在未读取文件的情况下编辑，此工具将报错。\n"
            "- 始终优先编辑代码库中的现有文件。除非明确要求，"
            "否则不要创建新文件。\n"
            "- 如果old_string在文件中不唯一，编辑将失败。"
            "要么提供更多上下文使其唯一，要么使用replace_all更改所有出现的old_string。\n"
            "- 使用replace_all在整个文件中替换和重命名字符串。"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要修改的文件的绝对路径（必须是绝对路径，不是相对路径）。",
                },
                "old_string": {
                    "type": "string",
                    "description": (
                        "要替换的文本。必须与文件内容精确匹配，"
                        "包括空格和缩进。"
                    ),
                },
                "new_string": {
                    "type": "string",
                    "description": "要替换成的文本（必须与old_string不同）。",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": (
                        "替换所有出现的old_string（默认为false）。 "
                        "如果为false且old_string出现多次，编辑将失败。"
                    ),
                    "default": False,
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    def permission_level(self, args: dict[str, Any]) -> str:
        return "write"

    async def call(self, args: dict[str, Any], context: ToolUseContext) -> ToolResult:
        file_path = args.get("file_path", "")
        old_string = args.get("old_string", "")
        new_string = args.get("new_string", "")
        replace_all = args.get("replace_all", False)

        if not file_path:
            given_keys = list(args.keys())
            return ToolResult(
                data=f"错误：'file_path'参数是必需的但未提供。 "
                     f"收到的参数：{given_keys}。 "
                     f"使用<arg_key>file_path</arg_key><arg_value>/绝对路径/到/文件</arg_value> "
                     f"<arg_key>old_string</arg_key><arg_value>要查找的文本</arg_value> "
                     f"<arg_key>new_string</arg_key><arg_value>替换文本</arg_value>",
                is_error=True,
            )
        if not old_string:
            given_keys = list(args.keys())
            return ToolResult(
                data=f"错误：'old_string'参数是必需的但未提供。 "
                     f"收到的参数：{given_keys}。 "
                     f"使用<arg_key>file_path</arg_key><arg_value>/绝对路径/到/文件</arg_value> "
                     f"<arg_key>old_string</arg_key><arg_value>要查找的文本</arg_value> "
                     f"<arg_key>new_string</arg_key><arg_value>替换文本</arg_value>",
                is_error=True,
            )
        if old_string == new_string:
            return ToolResult(data="错误：old_string和new_string相同。", is_error=True)

        # 解析相对路径并跟踪符号链接
        if not os.path.isabs(file_path):
            file_path = os.path.join(context.cwd, file_path)
        file_path = os.path.realpath(file_path)

        if not os.path.exists(file_path):
            return ToolResult(data=f"错误：找不到文件：{file_path}", is_error=True)

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as exc:
            return ToolResult(data=f"读取文件时出错：{exc}", is_error=True)

        count = content.count(old_string)

        if count == 0:
            preview = old_string[:120].replace("\n", "\\n")
            return ToolResult(
                data=(
                    f"错误：在{file_path}中找不到old_string。\n"
                    f"  搜索了（{len(old_string)}个字符）：\"{preview}\"\n"
                    f"  replace_all={replace_all}\n"
                    f"提示：首先读取文件以获取确切内容，"
                    f"包括空格和缩进。"
                ),
                is_error=True,
            )

        if not replace_all and count > 1:
            preview = old_string[:80].replace("\n", "\\n")
            return ToolResult(
                data=(
                    f"错误：old_string在{file_path}中出现{count}次。\n"
                    f"  搜索了：\"{preview}\"\n"
                    f"  replace_all={replace_all}\n"
                    f"提供更多上下文使匹配唯一，"
                    f"或设置replace_all=true替换所有出现。"
                ),
                is_error=True,
            )

        new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
        except Exception as exc:
            return ToolResult(data=f"写入文件时出错：{exc}", is_error=True)

        replacements = count if replace_all else 1
        diff_text = _make_diff(content, new_content, file_path)
        summary = f"Successfully replaced {replacements} occurrence(s) in {file_path}."
        return ToolResult(data=f"[ALAN-DIFF]\n{diff_text}\n{summary}")


def _make_diff(old: str, new: str, path: str, context: int = 3) -> str:
    """返回旧内容与新内容的统一差异。尾部换行符已修剪。"""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff_iter = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=path, tofile=path,
        n=context,
    )
    return "".join(diff_iter).rstrip("\n")
