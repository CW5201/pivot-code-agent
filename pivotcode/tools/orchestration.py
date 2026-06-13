"""工具调用编排 - 并发/串行批处理。

只读工具并发运行；修改工具串行运行。
权限回调从查询循环贯穿到每个工具调用。
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from pivotcode.messages.factory import create_tool_result_message
from pivotcode.messages.types import ToolUseBlock, UserMessage
from pivotcode.tools.base import Tool, ToolUseContext
from pivotcode.tools.execution import PermissionCallback, run_tool_use
from pivotcode.tools.registry import find_tool_by_name

logger = logging.getLogger(__name__)


@dataclass
class ToolUpdate:
    """编排中单个工具执行的结果。"""
    message: UserMessage | None = None  # tool_result消息
    tool_use_id: str = ""


@dataclass
class _Batch:
    """共享相同并发模式的工具使用块组。"""
    blocks: list[ToolUseBlock] = field(default_factory=list)
    is_concurrent: bool = False


# ---------------------------------------------------------------------------
# 分区
# ---------------------------------------------------------------------------


def partition_tool_calls(
    tool_use_blocks: list[ToolUseBlock],
    tools: list[Tool],
) -> list[_Batch]:
    """将工具调用分割为连续的只读（并发）
    和修改（串行）调用批次。

    连续的只读调用被分组到单个并发批次中。
    每个修改调用都有自己的串行批次（is_concurrent=False）。
    未知工具被视为修改工具以确保安全。
    """
    if not tool_use_blocks:
        return []

    batches: list[_Batch] = []
    current_batch: _Batch | None = None

    for block in tool_use_blocks:
        tool = find_tool_by_name(tools, block.name)
        is_ro = tool is not None and tool.permission_level(block.input) == "read"

        if is_ro:
            # 扩展或开始并发批次
            if current_batch is not None and current_batch.is_concurrent:
                current_batch.blocks.append(block)
            else:
                current_batch = _Batch(blocks=[block], is_concurrent=True)
                batches.append(current_batch)
        else:
            # 每个修改调用都是自己的串行批次
            current_batch = _Batch(blocks=[block], is_concurrent=False)
            batches.append(current_batch)

    return batches


# ---------------------------------------------------------------------------
# 执行辅助函数
# ---------------------------------------------------------------------------


async def _execute_single_tool(
    block: ToolUseBlock,
    tools: list[Tool],
    context: ToolUseContext,
    permission_callback: PermissionCallback | None = None,
) -> ToolUpdate:
    """查找工具，验证，执行，并将结果包装在ToolUpdate中。"""
    tool = find_tool_by_name(tools, block.name)

    if tool is None:
        msg = create_tool_result_message(
            tool_use_id=block.id,
            content=f"Unknown tool: {block.name}",
            is_error=True,
        )
        return ToolUpdate(message=msg, tool_use_id=block.id)

    message = await run_tool_use(
        tool_use=block,
        tool=tool,
        context=context,
        permission_callback=permission_callback,
    )
    return ToolUpdate(message=message, tool_use_id=block.id)


async def _run_tools_concurrently(
    blocks: list[ToolUseBlock],
    tools: list[Tool],
    context: ToolUseContext,
    *,
    max_concurrency: int = 10,
    permission_callback: PermissionCallback | None = None,
) -> AsyncGenerator[ToolUpdate, None]:
    """使用信号量并发运行一批只读工具调用。"""
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _guarded(block: ToolUseBlock) -> ToolUpdate:
        async with semaphore:
            return await _execute_single_tool(block, tools, context, permission_callback)

    tasks = [asyncio.create_task(_guarded(b)) for b in blocks]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    aborted = (
        context.abort_signal is not None and context.abort_signal.is_set()
    )

    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            block = blocks[i]
            if isinstance(result, asyncio.CancelledError) and aborted:
                # 用户取消（在UI提示处按Ctrl+C）。不是崩溃。
                logger.info("Tool %s cancelled by user", block.name)
                msg = create_tool_result_message(
                    tool_use_id=block.id,
                    content="Tool interrupted by user.",
                    is_error=True,
                )
            else:
                logger.error(
                    "Concurrent tool %s raised: %s", block.name, result, exc_info=result
                )
                msg = create_tool_result_message(
                    tool_use_id=block.id,
                    content=f"Tool execution error: {result}",
                    is_error=True,
                )
            yield ToolUpdate(message=msg, tool_use_id=block.id)
        else:
            yield result


async def _run_tools_serially(
    blocks: list[ToolUseBlock],
    tools: list[Tool],
    context: ToolUseContext,
    permission_callback: PermissionCallback | None = None,
) -> AsyncGenerator[ToolUpdate, None]:
    """一次运行一个工具调用。"""
    for block in blocks:
        update = await _execute_single_tool(block, tools, context, permission_callback)
        yield update


# ---------------------------------------------------------------------------
# 公共入口点
# ---------------------------------------------------------------------------


async def run_tools(
    tool_use_blocks: list[ToolUseBlock],
    tools: list[Tool],
    context: ToolUseContext,
    *,
    max_concurrency: int = 10,
    permission_callback: PermissionCallback | None = None,
) -> AsyncGenerator[ToolUpdate, None]:
    """执行工具调用，使用并发/串行批处理。

    将工具调用分区为批次:
    - 连续的只读工具 -> 并发运行（最多*max_concurrency*）
    - 修改工具 -> 一次运行一个

    在每次工具执行前调用*permission_callback*以检查
    权限（钩子先运行，然后是回调）。完整顺序请参见execution.py：
    验证 -> 钩子 -> 权限 -> tool.call() -> 后钩子。

    为每个完成的工具生成ToolUpdate。
    """
    for batch in partition_tool_calls(tool_use_blocks, tools):
        if batch.is_concurrent:
            async for update in _run_tools_concurrently(
                batch.blocks, tools, context,
                max_concurrency=max_concurrency,
                permission_callback=permission_callback,
            ):
                yield update
        else:
            async for update in _run_tools_serially(
                batch.blocks, tools, context,
                permission_callback=permission_callback,
            ):
                yield update
