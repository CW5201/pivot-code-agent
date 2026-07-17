"""Hook 注册表——生命周期事件钩子。

Hook 是用户定义的 shell 命令，在生命周期的特定节点执行。
在 .pivot/settings.json 的 "hooks" 项下配置。

示例配置：
{
  "hooks": {
    "PreToolUse": [
      {"command": "python check_safety.py", "tools": ["Bash"]}
    ],
    "PostToolUse": [
      {"command": "python auto_lint.py", "tools": ["Edit", "Write"]}
    ],
    "SessionStart": [
      {"command": "echo 'Pivot Code session started'"}
    ],
    "SessionEnd": [
      {"command": "echo 'Session ended'"}
    ]
  }
}

Hook 命令通过 stdin 接收包含事件上下文的 JSON 负载。
它们可以通过 stdout 返回 JSON 来影响行为：
  - PreToolUse: {"action": "allow"} | {"action": "deny", "message": "..."} | {"action": "ask"}
  - PostToolUse: （返回值被忽略，即发即弃）
  - SessionStart/End: （返回值被忽略）
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class HookType(str, Enum):
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"


HOOK_TIMEOUT_SECONDS = 30  # 一个 hook 可运行的最长时间


@dataclass
class HookConfig:
    """来自设置的单个 hook 定义。

    ``command`` 默认通过 :func:`asyncio.create_subprocess_exec` 执行，
    经 ``shlex.split`` 分词——不使用 shell，也不解释元字符。如需
    shell 解释（用于管道、重定向等），可在设置项中设置 ``shell: true``。
    """
    command: str
    tools: list[str] | None = None  # None = 所有工具，list = 仅这些工具
    timeout: int = HOOK_TIMEOUT_SECONDS
    shell: bool = False


@dataclass
class HookResult:
    """执行某个 hook 的结果。"""
    action: str = "allow"  # 'allow'、'deny'、'ask'、'passthrough'
    message: str = ""
    hook_name: str = ""
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


def load_hooks_from_settings(settings: dict[str, Any]) -> dict[HookType, list[HookConfig]]:
    """从设置字典中加载 hook 配置。"""
    hooks_raw = settings.get("hooks", {})
    if not isinstance(hooks_raw, dict):
        logger.warning("Invalid 'hooks' config: expected dict, got %s", type(hooks_raw).__name__)
        return {}

    result: dict[HookType, list[HookConfig]] = {}

    for type_name, hook_list in hooks_raw.items():
        # 从字符串解析出 HookType
        try:
            hook_type = HookType(type_name)
        except ValueError:
            logger.warning("Unknown hook type '%s', skipping", type_name)
            continue

        if not isinstance(hook_list, list):
            logger.warning("Hooks for '%s' should be a list, got %s", type_name, type(hook_list).__name__)
            continue

        configs: list[HookConfig] = []
        for entry in hook_list:
            if isinstance(entry, str):
                # 简写形式：仅一个命令字符串
                configs.append(HookConfig(command=entry))
            elif isinstance(entry, dict):
                command = entry.get("command")
                if not command:
                    logger.warning("Hook entry in '%s' missing 'command', skipping", type_name)
                    continue
                configs.append(HookConfig(
                    command=command,
                    tools=entry.get("tools"),
                    timeout=entry.get("timeout", HOOK_TIMEOUT_SECONDS),
                    shell=bool(entry.get("shell", False)),
                ))
            else:
                logger.warning("Invalid hook entry in '%s': %r", type_name, entry)

        if configs:
            result[hook_type] = configs

    return result


async def execute_hook(
    hook_type: HookType,
    hook: HookConfig,
    payload: dict[str, Any],
) -> HookResult:
    """执行单个 hook 命令。

    通过 stdin 以 JSON 形式发送负载。
    将 stdout 解析为结果 JSON（用于 PreToolUse）。
    遵守超时设置。
    """
    import shlex

    result = HookResult(hook_name=hook.command)
    payload_bytes = json.dumps(payload).encode()
    # 若 PreToolUse hook 执行失败，回退为 ASK（呈现给用户）
    # 而非 ALLOW。一个崩溃的安全关键 hook 绝不能与一个成功的
    # allow 无法区分。
    safe_failure_action = "ask" if hook_type == HookType.PRE_TOOL_USE else "allow"

    try:
        if hook.shell or os.name == "nt":
            # 选择启用 shell 执行。这是被记录为有风险的路径。
            # 在 Windows 上，为兼容性始终使用 shell 执行。
            proc = await asyncio.create_subprocess_shell(
                hook.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            # 默认：argv 风格的执行。不解释 shell 元字符。
            try:
                argv = shlex.split(hook.command)
            except ValueError as exc:
                logger.warning(
                    "Hook '%s' failed to tokenize: %s. Set 'shell: true' if "
                    "shell interpretation is intended.",
                    hook.command, exc,
                )
                result.exit_code = -1
                result.stderr = f"Tokenization failed: {exc}"
                result.action = safe_failure_action
                return result
            if not argv:
                result.exit_code = -1
                result.stderr = "Empty command"
                result.action = safe_failure_action
                return result
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=payload_bytes),
                timeout=hook.timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("Hook '%s' timed out after %ds", hook.command, hook.timeout)
            result.exit_code = -1
            result.stderr = f"Hook timed out after {hook.timeout}s"
            result.action = safe_failure_action
            return result

        result.exit_code = proc.returncode or 0
        result.stdout = stdout_bytes.decode(errors="replace").strip()
        result.stderr = stderr_bytes.decode(errors="replace").strip()

        if result.stderr:
            logger.debug("Hook '%s' stderr: %s", hook.command, result.stderr)

        # 将 stdout 作为 JSON 解析（针对 PreToolUse hook）
        if hook_type == HookType.PRE_TOOL_USE and result.stdout:
            try:
                data = json.loads(result.stdout)
                if isinstance(data, dict):
                    result.action = data.get("action", "allow")
                    result.message = data.get("message", "")
            except json.JSONDecodeError:
                logger.debug(
                    "Hook '%s' stdout is not valid JSON, treating as allow: %s",
                    hook.command, result.stdout[:200],
                )

        # PreToolUse 的非零退出码 => 拒绝
        if hook_type == HookType.PRE_TOOL_USE and result.exit_code != 0 and result.action == "allow":
            result.action = "deny"
            if not result.message:
                result.message = f"Hook '{hook.command}' exited with code {result.exit_code}"

    except Exception as exc:
        logger.warning("Failed to execute hook '%s': %s", hook.command, exc)
        result.exit_code = -1
        result.stderr = str(exc)
        result.action = safe_failure_action

    return result


async def run_hooks(
    hook_type: HookType,
    payload: dict[str, Any],
    settings: dict[str, Any] | None = None,
    tool_name: str | None = None,
) -> list[HookResult]:
    """运行某个给定类型的所有 hook。

    如果提供了 tool_name，则只运行匹配该工具的 hook。
    返回结果列表。
    """
    if settings is None:
        settings = {}

    hooks_by_type = load_hooks_from_settings(settings)
    hooks = hooks_by_type.get(hook_type, [])

    if not hooks:
        return []

    # 若指定了 tool_name，则按工具名过滤
    if tool_name is not None:
        hooks = [
            h for h in hooks
            if h.tools is None or tool_name in h.tools
        ]

    if not hooks:
        return []

    results: list[HookResult] = []
    for hook in hooks:
        result = await execute_hook(hook_type, hook, payload)
        results.append(result)

    return results


async def run_pre_tool_hooks(
    tool_name: str,
    tool_input: dict[str, Any],
    settings: dict[str, Any] | None = None,
) -> HookResult | None:
    """运行 PreToolUse hook。返回第一个拒绝/询问结果；若全部允许则返回 None。"""
    payload = {
        "hook_type": HookType.PRE_TOOL_USE.value,
        "tool_name": tool_name,
        "tool_input": tool_input,
    }

    results = await run_hooks(
        HookType.PRE_TOOL_USE,
        payload,
        settings=settings,
        tool_name=tool_name,
    )

    for result in results:
        if result.action in ("deny", "ask"):
            return result

    return None


async def run_post_tool_hooks(
    tool_name: str,
    tool_input: dict[str, Any],
    tool_output: str,
    is_error: bool = False,
    settings: dict[str, Any] | None = None,
) -> None:
    """运行 PostToolUse hook（即发即弃）。"""
    hook_type = HookType.POST_TOOL_USE_FAILURE if is_error else HookType.POST_TOOL_USE

    payload = {
        "hook_type": hook_type.value,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output": tool_output,
        "is_error": is_error,
    }

    # 即便在失败时，也同时运行通用的 PostToolUse hook
    await run_hooks(hook_type, payload, settings=settings, tool_name=tool_name)
    if is_error:
        await run_hooks(HookType.POST_TOOL_USE, payload, settings=settings, tool_name=tool_name)
