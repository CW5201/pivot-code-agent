"""GrepTool - 使用正则表达式搜索内容（通过ripgrep、grep或纯Python回退）。"""

import asyncio
import fnmatch
import os
import pathlib
import re
import shutil
from typing import Any

from pivotcode.tools.base import Tool, ToolResult, ToolUseContext

_DEFAULT_HEAD_LIMIT = 250


class GrepTool(Tool):
    """通过ripgrep、grep或纯Python回退，使用正则表达式模式搜索文件内容。"""

    @property
    def name(self) -> str:
        return "Grep"

    @property
    def description(self) -> str:
        return (
            "使用正则表达式模式搜索文件内容。当可用时支持ripgrep语法，"
            "否则回退到grep或纯Python。\n\n"
            "用法:\n"
            "- 用于在代码库中搜索代码、配置和模式\n"
            "- 支持完整的正则表达式语法（例如，'log.*Error'，'function\\s+\\w+'）\n"
            "- 使用glob参数过滤文件（例如，'*.py'，'*.{ts,tsx}'）\n"
            "- 输出模式：'files_with_matches'（默认，仅路径），"
            "'content'（匹配行），'count'（匹配计数）"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "要在文件内容中搜索的正则表达式模式。",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "要搜索的文件或目录。默认为当前工作目录。 "
                        "用于目录范围（例如'src/'仅搜索src/）。"
                    ),
                },
                "glob": {
                    "type": "string",
                    "description": (
                        "用于过滤文件的glob模式（例如'*.py'，'*.{ts,tsx}'）。 "
                        "不要在此处包含目录前缀——请使用'path'。"
                    ),
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_with_matches", "count"],
                    "description": (
                        "输出模式：'content'显示带上下文的匹配行，"
                        "'files_with_matches'仅显示文件路径（默认），"
                        "'count'显示每个文件的匹配计数。"
                    ),
                },
                "context": {
                    "type": "integer",
                    "description": (
                        "每个匹配前后要显示的行数。"
                        "仅当output_mode为'content'时适用。"
                    ),
                },
            },
            "required": ["pattern"],
        }

    def permission_level(self, args: dict[str, Any]) -> str:
        return "read"

    async def call(self, args: dict[str, Any], context: ToolUseContext) -> ToolResult:
        pattern = args.get("pattern", "")
        search_path = args.get("path", "") or context.cwd
        file_glob = args.get("glob", "")
        output_mode = args.get("output_mode", "files_with_matches")
        ctx_lines = args.get("context", None)

        if not pattern:
            given_keys = list(args.keys())
            return ToolResult(
                data=f"错误：'pattern'参数是必需的但未提供。 "
                     f"收到的参数：{given_keys}。 "
                     f"使用<arg_key>pattern</arg_key><arg_value>您的正则表达式模式</arg_value>",
                is_error=True,
            )

        if not os.path.isabs(search_path):
            search_path = os.path.join(context.cwd, search_path)
        # 规范化：/foo/bar/. → /foo/bar
        search_path = os.path.normpath(search_path)

        if not os.path.exists(search_path):
            return ToolResult(data=f"错误：找不到路径：{search_path}", is_error=True)

        # 先尝试 rg，然后 grep，最后回退到纯 Python
        rg_path = shutil.which("rg")
        grep_path = shutil.which("grep")

        if rg_path:
            result = await self._run_rg(rg_path, pattern, search_path, file_glob, output_mode, ctx_lines)
        elif grep_path:
            result = await self._run_grep(grep_path, pattern, search_path, file_glob, output_mode, ctx_lines)
        else:
            result = await self._python_fallback(pattern, search_path, file_glob, output_mode, ctx_lines)

        return result

    async def _run_rg(
        self, rg: str, pattern: str, path: str, file_glob: str,
        mode: str, ctx_lines: int | None,
    ) -> ToolResult:
        """使用给定的搜索参数构建并运行ripgrep命令。"""
        cmd = [rg, "--no-heading", "-n"]

        if mode == "files_with_matches":
            cmd.append("-l")
        elif mode == "count":
            cmd.append("-c")

        if ctx_lines is not None and mode == "content":
            cmd.extend(["-C", str(ctx_lines)])

        if file_glob:
            cmd.extend(["--glob", file_glob])

        cmd.extend(["--", pattern, path])
        return await self._run_subprocess(cmd)

    async def _run_grep(
        self, grep_bin: str, pattern: str, path: str, file_glob: str,
        mode: str, ctx_lines: int | None,
    ) -> ToolResult:
        """使用给定的搜索参数构建并运行GNU grep命令。"""
        cmd = [grep_bin, "-rn", "-E"]

        if mode == "files_with_matches":
            cmd.append("-l")
        elif mode == "count":
            cmd.append("-c")

        if ctx_lines is not None and mode == "content":
            cmd.extend(["-C", str(ctx_lines)])

        if file_glob:
            # GNU grep的--include不支持**/（双星递归）。
            # 由于-r已经递归，去除任何前导**/前缀。
            # 例如，"**/*.py" → "*.py"，"**/src/**/*.ts" → "*.ts"
            clean_glob = file_glob
            while clean_glob.startswith("**/"):
                clean_glob = clean_glob[3:]
            # 如果中间仍有**/，取最后一段
            if "**/" in clean_glob:
                clean_glob = clean_glob.rsplit("**/", 1)[-1]
            cmd.extend(["--include", clean_glob])

        cmd.extend(["--", pattern, path])
        return await self._run_subprocess(cmd)

    async def _run_subprocess(self, cmd: list[str]) -> ToolResult:
        """执行搜索子进程并将其输出作为ToolResult返回。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except TimeoutError:
            return ToolResult(data="搜索在30秒后超时。", is_error=True)
        except Exception as exc:
            return ToolResult(data=f"运行搜索时出错：{exc}", is_error=True)

        output = stdout.decode("utf-8", errors="replace").rstrip()

        if proc.returncode == 1 and not output:
            return ToolResult(data="没有找到匹配项。")

        if proc.returncode not in (0, 1):
            err = stderr.decode("utf-8", errors="replace").rstrip()
            return ToolResult(data=f"搜索错误（退出码{proc.returncode}）：{err}", is_error=True)

        # 截断到头部限制
        lines = output.split("\n")
        if len(lines) > _DEFAULT_HEAD_LIMIT:
            output = "\n".join(lines[:_DEFAULT_HEAD_LIMIT])
            output += f"\n\n(已截断 - 显示{len(lines)}行中的{_DEFAULT_HEAD_LIMIT}行。)"

        return ToolResult(data=output if output else "没有找到匹配项。")

    async def _python_fallback(
        self, pattern: str, path: str, file_glob: str,
        mode: str, ctx_lines: int | None,
    ) -> ToolResult:
        """当rg和grep都不可用时的纯Python回退。

        墙上时钟限制为30秒。在大型树上这很重要 - 没有
        限制，代理将挂起直到完整的文件系统遍历完成。
        """
        try:
            return await asyncio.wait_for(
                self._python_fallback_impl(
                    pattern, path, file_glob, mode, ctx_lines,
                ),
                timeout=30.0,
            )
        except TimeoutError:
            return ToolResult(
                data="Python回退搜索在30秒后超时。"
                     "请安装`ripgrep`（`rg`）或GNU `grep`以加快搜索速度，"
                     "或缩小模式/路径范围。",
                is_error=True,
            )

    async def _python_fallback_impl(
        self, pattern: str, path: str, file_glob: str,
        mode: str, ctx_lines: int | None,
    ) -> ToolResult:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return ToolResult(data=f"无效的正则表达式：{exc}", is_error=True)

        base = pathlib.Path(path)
        files = [base] if base.is_file() else sorted(base.rglob("*"))

        results: list[str] = []
        for fp in files:
            if not fp.is_file():
                continue
            if file_glob and not fnmatch.fnmatch(fp.name, file_glob):
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            if mode == "files_with_matches":
                if regex.search(text):
                    results.append(str(fp))
            elif mode == "count":
                cnt = len(regex.findall(text))
                if cnt:
                    results.append(f"{fp}:{cnt}")
            else:
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        results.append(f"{fp}:{i}:{line}")

            if len(results) >= _DEFAULT_HEAD_LIMIT:
                break
            # 协作式检查点，以便 asyncio.wait_for 能够中断我们。
            await asyncio.sleep(0)

        if not results:
            return ToolResult(data="没有找到匹配项。")

        output = "\n".join(results[:_DEFAULT_HEAD_LIMIT])
        if len(results) > _DEFAULT_HEAD_LIMIT:
            output += f"\n\n(截断到{_DEFAULT_HEAD_LIMIT}个结果。)"
        return ToolResult(data=output)
