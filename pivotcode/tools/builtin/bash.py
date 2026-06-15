"""BashTool - 通过asyncio子进程执行shell命令。"""

import asyncio
import os
from pathlib import Path
from typing import Any

from pivotcode.tools.base import Tool, ToolResult, ToolUseContext


class BashTool(Tool):
    """通过asyncio子进程执行shell命令。"""

    @property
    def name(self) -> str:
        return "Bash"

    @property
    def description(self) -> str:
        return (
            "执行给定的bash命令并返回其输出。\n\n"
            "每次调用时工作目录设置为项目根目录。 "
            "stdout和stderr在输出中合并。\n\n"
            "重要：避免使用此工具运行cat、head、tail、sed、awk、"
            "或echo命令，除非明确指示。相反，请使用"
            "适当的专用工具（Read、Edit、Write、Glob、Grep），因为它们"
            "提供更好的体验。\n\n"
            "对于快速Python代码片段，请使用：python3 -c '<code>'"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "要在bash shell中执行的命令。 "
                        "使用'&&'链接顺序命令。 "
                        "始终引用包含空格的文件路径。"
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "可选的超时时间（毫秒）（默认120000，即2分钟）。 "
                        "如果命令超过此持续时间，将被终止。"
                    ),
                },
                "purpose": {
                    "type": "string",
                    "description": (
                        "此命令功能的清晰、简洁的单行摘要。 "
                        "在批准前显示给用户。"
                    ),
                },
            },
            "required": ["command"],
        }

    def permission_level(self, args: dict[str, Any]) -> str:
        return "exec"

    async def call(self, args: dict[str, Any], context: ToolUseContext) -> ToolResult:
        command = args.get("command", "")
        if not command.strip():
            given_keys = list(args.keys())
            return ToolResult(
                data=f"错误：'command'参数是必需的但未提供。 "
                     f"收到的参数：{given_keys}。 "
                     f"使用<arg_key>command</arg_key><arg_value>您的命令</arg_value>",
                is_error=True,
            )

        timeout_ms = args.get("timeout", 120_000)
        if not isinstance(timeout_ms, (int, float)) or timeout_ms <= 0:
            timeout_ms = 120_000
        timeout_s = timeout_ms / 1000.0

        # Windows 上把垫片目录加到 PATH 前面，提供 ls/cat/head/find 等
        # 常见 Unix 命令，让 Bash 工具跨平台可用。
        env = dict(os.environ)
        if os.name == "nt":
            shim_dir = Path(__file__).parent / "win_shims"
            if shim_dir.is_dir():
                env["PATH"] = str(shim_dir) + os.pathsep + env.get("PATH", "")

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=context.cwd,
                env=env,
            )
        except Exception as exc:
            return ToolResult(data=f"启动进程失败：{exc}", is_error=True)

        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
        except TimeoutError:
            process.kill()
            await process.wait()
            return ToolResult(
                data=f"命令在{timeout_ms}ms后超时并被终止。",
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(data=f"执行期间出错：{exc}", is_error=True)

        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        exit_code = process.returncode

        # 修剪尾部空格但保留结构
        output = output.rstrip()

        if exit_code == 0:
            return ToolResult(data=output if output else "(no output)")
        else:
            text = output + (f"\n\nExit code: {exit_code}" if output else f"Exit code: {exit_code}")
            return ToolResult(data=text, is_error=(exit_code != 0))
