"""单个工具执行，包含验证和权限检查。"""

import logging
from collections.abc import Awaitable, Callable

from pivotcode.hooks.registry import run_post_tool_hooks, run_pre_tool_hooks
from pivotcode.messages.factory import create_tool_result_message
from pivotcode.messages.types import ToolUseBlock, UserMessage
from pivotcode.permissions.context import PermissionBehavior, PermissionResult
from pivotcode.tools.base import Tool, ToolResult, ToolUseContext

logger = logging.getLogger(__name__)

# 权限回调的类型别名
PermissionCallback = Callable[
    [Tool, dict, ToolUseContext], Awaitable[PermissionResult]
]


async def run_tool_use(
    tool_use: ToolUseBlock,
    tool: Tool,
    context: ToolUseContext,
    permission_callback: PermissionCallback | None = None,
) -> UserMessage:
    """执行单个工具调用，包含验证和权限检查。

    步骤:
    1. 通过tool.validate_input()验证输入
    2. 通过permission_callback检查权限（如果提供）
    3. 通过tool.call()执行工具
    4. 构建并返回tool_result消息

    任何错误时，返回错误的tool_result消息。
    """
    tool_use_id = tool_use.id
    args = tool_use.input

    # 1. 验证输入
    try:
        validation_error = tool.validate_input(args, context)
    except Exception as exc:
        logger.error("Validation crashed for tool %s: %s", tool.name, exc)
        return _error_result(tool_use_id, f"Input validation error: {exc}")

    if validation_error is not None:
        logger.warning(
            "Validation failed for tool %s: %s", tool.name, validation_error
        )
        return _error_result(tool_use_id, validation_error)

    # 2. 工具使用前钩子
    try:
        hook_result = await run_pre_tool_hooks(tool.name, args, settings=context.settings)
    except Exception as exc:
        logger.error("Pre-tool hook crashed for tool %s: %s", tool.name, exc)
        hook_result = None

    if hook_result is not None:
        if hook_result.action == "deny":
            message = hook_result.message or f"Blocked by hook: {hook_result.hook_name}"
            logger.info("Tool %s denied by hook: %s", tool.name, message)
            return _error_result(tool_use_id, message)
        # "ask"会传递到下面的正常权限检查

    # 3. 检查权限（来自钩子的ASK会传递到这里）
    if permission_callback is not None:
        try:
            perm = await permission_callback(tool, args, context)
        except Exception as exc:
            logger.error(
                "Permission callback crashed for tool %s: %s", tool.name, exc
            )
            return _error_result(
                tool_use_id, f"Permission check error: {exc}"
            )

        if perm.behavior == PermissionBehavior.DENY:
            message = perm.message or "Permission denied."
            logger.info("Tool %s denied: %s", tool.name, message)
            return _error_result(tool_use_id, message)

        if perm.behavior == PermissionBehavior.ASK:
            # 在此层中，没有明确ALLOW的ASK被视为拒绝。
            # 上层代码应在调用run_tool_use之前解决ASK。
            message = perm.message or "Tool use requires approval but was not approved."
            logger.info("Tool %s requires approval: %s", tool.name, message)
            return _error_result(tool_use_id, message)

        # 如果权限钩子修改了输入，使用更新后的版本
        if perm.updated_input is not None:
            args = perm.updated_input

    # 4. 执行工具
    try:
        result: ToolResult = await tool.call(args, context)
    except Exception as exc:
        logger.error("Tool %s execution failed: %s", tool.name, exc, exc_info=True)
        # 触发失败后的工具后钩子（发射即忘）
        try:
            await run_post_tool_hooks(
                tool.name, args, str(exc), is_error=True, settings=context.settings,
            )
        except Exception:
            logger.debug("Post-tool hook error (ignored)", exc_info=True)
        return _error_result(tool_use_id, f"Tool execution error: {exc}")

    # 5. 工具使用后钩子（发射即忘）
    content = _result_to_str(result)
    try:
        await run_post_tool_hooks(
            tool.name, args, content, is_error=result.is_error, settings=context.settings,
        )
    except Exception:
        logger.debug("Post-tool hook error (ignored)", exc_info=True)

    # 6. 构建tool_result消息
    return create_tool_result_message(
        tool_use_id=tool_use_id,
        content=content,
        is_error=result.is_error,
    )


def _error_result(tool_use_id: str, message: str) -> UserMessage:
    """构建错误的tool_result消息。"""
    return create_tool_result_message(
        tool_use_id=tool_use_id,
        content=message,
        is_error=True,
    )


def _result_to_str(result: ToolResult) -> str:
    """将ToolResult的数据转换为适合API的字符串。"""
    if isinstance(result.data, str):
        return result.data
    if result.data is None:
        return ""
    return str(result.data)
