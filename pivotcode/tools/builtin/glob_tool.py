"""GlobTool - 文件模式匹配。"""

import asyncio
import os
import pathlib
from typing import Any

from pivotcode.tools.base import Tool, ToolResult, ToolUseContext

_MAX_RESULTS = 1000
_DEFAULT_TIMEOUT_MS = 30_000  # 30 seconds


class GlobTool(Tool):
    """通过glob模式查找文件，按修改时间排序。"""

    @property
    def name(self) -> str:
        return "Glob"

    @property
    def description(self) -> str:
        return (
            "快速文件模式匹配工具，适用于任何代码库大小。 "
            "支持glob模式，如'**/*.py'或'src/**/*.ts'。 "
            "返回匹配的文件路径，按修改时间排序（最新优先）。 "
            "当您需要按名称或扩展名模式查找文件时使用此工具。"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "用于匹配文件的glob模式（例如，'**/*.py'，'src/**/*.ts'）。",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "要搜索的目录。如果未指定，将使用当前工作目录。 "
                        "如果提供，必须是有效的目录路径。"
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "可选的超时时间（毫秒）（默认30000）。 "
                        "如果搜索超过此持续时间，将停止。"
                    ),
                },
            },
            "required": ["pattern"],
        }

    def permission_level(self, args: dict[str, Any]) -> str:
        return "read"

    async def call(self, args: dict[str, Any], context: ToolUseContext) -> ToolResult:
        """执行glob搜索并具有超时保护。"""
        pattern = args.get("pattern", "")
        search_path = args.get("path", "") or context.cwd
        timeout_ms = args.get("timeout", _DEFAULT_TIMEOUT_MS)
        if not isinstance(timeout_ms, (int, float)) or timeout_ms <= 0:
            timeout_ms = _DEFAULT_TIMEOUT_MS
        timeout_s = timeout_ms / 1000.0

        if not pattern:
            given_keys = list(args.keys())
            return ToolResult(
                data=f"错误：'pattern'参数是必需的但未提供。 "
                     f"收到的参数：{given_keys}。 "
                     f"使用<arg_key>pattern</arg_key><arg_value>您的模式</arg_value>",
                is_error=True,
            )

        if not os.path.isabs(search_path):
            search_path = os.path.join(context.cwd, search_path)

        if not os.path.isdir(search_path):
            return ToolResult(data=f"错误：找不到目录：{search_path}", is_error=True)

        base = pathlib.Path(search_path)

        # 扫描超出_MAX_RESULTS的额外块，以便调用者可以区分
        # "恰好_MAX_RESULTS个文件"和"存在超过_MAX_RESULTS个文件，但我只保留了前几个"。
        scan_cap = _MAX_RESULTS * 2

        def _run_glob():
            matches = []
            for p in base.glob(pattern):
                if p.is_file():
                    try:
                        mtime = p.stat().st_mtime
                    except OSError:
                        mtime = 0.0
                    matches.append((str(p), mtime))
                    if len(matches) >= scan_cap:
                        break
            return matches

        try:
            matches = await asyncio.wait_for(
                asyncio.to_thread(_run_glob),
                timeout=timeout_s,
            )
        except TimeoutError:
            return ToolResult(
                data=f"Glob搜索在{timeout_ms}ms后超时。请尝试更具体的模式。",
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(data=f"Glob期间出错：{exc}", is_error=True)

        # 按修改时间排序，最新优先
        matches.sort(key=lambda x: x[1], reverse=True)

        if not matches:
            return ToolResult(data=f"在{search_path}中没有文件匹配模式'{pattern}'")

        total_seen = len(matches)
        truncated = total_seen > _MAX_RESULTS
        hit_scan_cap = total_seen >= scan_cap
        matches = matches[:_MAX_RESULTS]
        paths = [m[0] for m in matches]

        output = "\n".join(paths)
        if truncated:
            if hit_scan_cap:
                # 我们在scan_cap处停止扫描 - 真实匹配计数可能比
                # 我们能报告的更高。告诉模型，以便它缩小模式范围
                # 而不是将scan_cap视为真实情况。
                output += (
                    f"\n\n(显示{scan_cap}+个匹配中的前{_MAX_RESULTS}个。"
                    f"缩小模式范围 - 真实计数可能更高。)"
                )
            else:
                output += (
                    f"\n\n(显示{total_seen}个匹配中的前{_MAX_RESULTS}个。)"
                )

        return ToolResult(data=output)
