"""GitCommit工具 - 将文件暂存并提交到git。

创建在会话状态中跟踪为"Pivot commit"的提交，
支持AGT（Agentic Git Tree）可视化和导航。
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

from pivotcode.tools.base import Tool, ToolResult, ToolUseContext


class GitCommitTool(Tool):
    """暂存文件并创建git提交。"""

    @property
    def name(self) -> str:
        return "GitCommit"

    @property
    def description(self) -> str:
        return (
            "将文件暂存并提交到git，附带提交消息。 "
            "如果未指定文件，所有更改都将被暂存（git add -A）。 "
            "提交在会话中被跟踪，用于历史可视化。"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "提交消息。",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "提交前要暂存的文件。 "
                        "如果省略，所有更改都将被暂存（git add -A）。"
                    ),
                },
                "allow_empty": {
                    "type": "boolean",
                    "description": (
                        "允许创建没有文件更改的提交 "
                        "（例如，当只有内存更新时）。默认为false。"
                    ),
                },
            },
            "required": ["message"],
        }

    def permission_level(self, args: dict[str, Any]) -> str:
        return "write"

    async def call(self, args: dict[str, Any], context: ToolUseContext) -> ToolResult:
        cwd = context.cwd
        message = args.get("message", "")
        files = args.get("files", [])
        allow_empty = args.get("allow_empty", False)

        if not message:
            return ToolResult(data="错误：需要提交消息。", is_error=True)

        # 检查 git 仓库
        from pivotcode.utils.env import is_git_repo
        if not is_git_repo(cwd):
            return ToolResult(data="错误：不是git仓库。", is_error=True)

        # 检查合并冲突
        merge_head = os.path.join(cwd, ".git", "MERGE_HEAD")
        if os.path.exists(merge_head):
            return ToolResult(
                data="错误：合并正在进行。请先解决冲突。",
                is_error=True,
            )

        # 暂存文件
        if files:
            for f in files:
                result = self._run_git(cwd, "add", f)
                if result.returncode != 0:
                    return ToolResult(
                        data=f"暂存{f}时出错：{result.stderr.strip()}",
                        is_error=True,
                    )
        else:
            result = self._run_git(cwd, "add", "-A")
            if result.returncode != 0:
                return ToolResult(
                    data=f"暂存文件时出错：{result.stderr.strip()}",
                    is_error=True,
                )

        # 检查是否有可提交的内容
        status = self._run_git(cwd, "status", "--porcelain")
        if not allow_empty and not status.stdout.strip():
            return ToolResult(
                data="没有可提交的内容 - 工作树干净。",
                is_error=True,
            )

        # 提交
        commit_cmd = ["commit", "-m", message]
        if allow_empty:
            commit_cmd.append("--allow-empty")
        result = self._run_git(cwd, *commit_cmd)
        if result.returncode != 0:
            return ToolResult(
                data=f"提交时出错：{result.stderr.strip()}",
                is_error=True,
            )

        # 获取新的提交 SHA
        sha_result = self._run_git(cwd, "rev-parse", "HEAD")
        if sha_result.returncode != 0:
            return ToolResult(
                data="提交成功但获取SHA失败。",
                is_error=True,
            )
        new_sha = sha_result.stdout.strip()
        short_sha = new_sha[:7]

        # 获取分支名称
        branch_result = self._run_git(cwd, "symbolic-ref", "--short", "HEAD")
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "detached"

        # 更新会话状态（AGT 跟踪）
        state = context.session_state
        if state is not None:
            try:
                with state.batch():
                    state.add_pivot_commit(new_sha)
                    state.add_to_conv_path(new_sha)
                    state.agent_position_sha = new_sha
                    # 记录消息计数，以便 /convrevert 能精确截断
                    state.record_commit_message_index(
                        new_sha, len(context.messages),
                    )
            except Exception:
                pass  # AGT tracking is non-critical

        return ToolResult(
            data=f"Committed {short_sha} on {branch}: {message}",
        )

    @staticmethod
    def _run_git(cwd: str, *args: str) -> subprocess.CompletedProcess:
        """在给定目录中运行git命令。"""
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
