"""代理查询循环。

Pivot Code 的核心 —— 一个 while-true 的异步生成器，它执行以下步骤：
1. 准备消息（压缩管道）
2. 调用 LLM（流式传输）
3. 处理响应
4. 如果请求则执行工具
5. 循环回到开始

有关分阶段的详细说明，请参阅 docs/architecture/query-loop.md。
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pivotcode.api.cost_tracker import CostTracker
from pivotcode.api.retry import stream_with_retry
from pivotcode.compact.compact_auto import compaction_auto
from pivotcode.compact.compact_clear import compaction_clear_tool_results
from pivotcode.compact.compact_truncate import compaction_truncate_tool_results
from pivotcode.messages.factory import (
    create_assistant_error_message,
    create_attachment_message,
    create_user_interruption_message,
    create_user_message,
)
from pivotcode.messages.normalization import normalize_messages_for_api
from pivotcode.messages.serialization import messages_to_openai_dicts
from pivotcode.messages.types import (
    AssistantContentBlock,
    AssistantMessage,
    Message,
    QueryYield,
    RequestStartEvent,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Usage,
    UserMessage,
    get_messages_after_compact_boundary,
)
from pivotcode.providers.base import (
    LLMProvider,
    StreamError,
    StreamMessageDelta,
    StreamMessageStart,
    StreamTextDelta,
    StreamThinkingDelta,
    StreamToolUseInputDelta,
    StreamToolUseStart,
    StreamToolUseStop,
    ToolSchema,
)
from pivotcode.query.state import LoopState
from pivotcode.settings import SETTINGS_DEFAULTS
from pivotcode.tools.base import Tool, ToolUseContext
from pivotcode.tools.orchestration import run_tools
from pivotcode.tools.registry import tools_to_schemas
from pivotcode.tools.text_tool_parser import MAX_TEXT_TOOL_RETRIES, extract_tool_calls_from_text
from pivotcode.utils.tokens import predicted_next_call_tokens

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 系统提醒 —— 在迭代之间注入为 <system-reminder> 消息
# ---------------------------------------------------------------------------


def _build_turn_reminders(context: ToolUseContext) -> list[UserMessage]:
    """构建在每轮开始时注入一次的系统提醒。

    包含：当前日期和时间（精确到分钟）。
    这些补充系统提示词中的日期（在整个会话期间固定）。
    标记为 hide_in_ui=True。在压缩期间可以安全丢弃。
    """
    now = datetime.now(UTC).astimezone()
    date_str = now.strftime("%Y-%m-%d %H:%M")

    reminder_text = (
        "<system-reminder>\n"
        f"# currentDateTime\nCurrent date and time: {date_str}\n"
        "</system-reminder>"
    )
    return [create_user_message(reminder_text, hide_in_ui=True)]




def _drain_message_queue(msg_queue) -> list[UserMessage]:
    """将 inject_message() 中的排队消息清空为用户消息。

    接受 ``queue.SimpleQueue`` 或普通列表。
    消息被消费并包装为用户消息。
    """
    import queue as _queue_mod

    if msg_queue is None:
        return []

    messages: list[UserMessage] = []
    if isinstance(msg_queue, _queue_mod.SimpleQueue):
        while not msg_queue.empty():
            try:
                text = msg_queue.get_nowait()
                messages.append(create_user_message(text))
            except _queue_mod.Empty:
                break
    elif isinstance(msg_queue, list):
        while msg_queue:
            text = msg_queue.pop(0)
            messages.append(create_user_message(text))

    return messages


# ---------------------------------------------------------------------------
# 查询参数
# ---------------------------------------------------------------------------


@dataclass
class QueryParams:
    """查询循环的参数。"""
    messages: list[Message]
    system_prompt: list[str]
    provider: LLMProvider
    tools: list[Tool]
    context: ToolUseContext
    cost_tracker: CostTracker
    model: str | None = None
    system_static_boundary: int = 0
    max_iterations_per_turn: int | None = None
    max_output_tokens: int | None = None
    # 记忆模式
    memory_mode: str = "on"  # "on", "off", "intensive"
    # 权限回调
    permission_callback: Any = None  # 异步函数 (tool, input, context) -> PermissionResult
    # 中止信号
    abort_event: asyncio.Event | None = None
    # 来自 ask_while_running / inject_message 的排队消息（共享列表引用）
    message_queue: list[str] | None = None
    # 完整设置字典（所有参数，扁平化）
    settings: dict = None  # type: ignore[assignment]
    # 预调用 token 估算的种子值：上次 API 调用报告的
    # 使用量（在恢复时持久化）。在新代理中为零。
    last_input_tokens_seed: int = 0
    last_output_tokens_seed: int = 0
    # LLM 视角回调（在每次 API 调用前调用，传入 api_messages_dicts）
    llm_perspective_callback: Any = None  # 可调用对象 Callable[[list[dict]], None] | None

    def __post_init__(self):
        if self.settings is None:
            self.settings = dict(SETTINGS_DEFAULTS)



# ---------------------------------------------------------------------------
# 代理循环
# ---------------------------------------------------------------------------


async def query_loop(params: QueryParams) -> AsyncGenerator[QueryYield, None]:
    """主代理循环。生成流事件和消息。

    调用者将此生成器迭代至完成。内部 while 循环的每次迭代对应一次
    LLM 往返（可能随后跟着工具执行）。
    """
    state = LoopState(
        messages=list(params.messages),
        last_input_tokens=params.last_input_tokens_seed,
        last_output_tokens=params.last_output_tokens_seed,
        messages_len_at_last_call=(
            len(params.messages) if params.last_input_tokens_seed > 0 else 0
        ),
    )
    iteration = 0

    while True:
        # -- 阶段 1：检查中止 ----------------------------------------
        if params.abort_event and params.abort_event.is_set():
            yield create_user_interruption_message(tool_use=False)
            return

        yield RequestStartEvent()

        # -- 阶段 1.5：注入系统提醒 --------------------------
        injected: list[UserMessage] = []

        # 轮次提醒（日期+时间）：仅在每轮的第一次迭代时
        if iteration == 0:
            for reminder in _build_turn_reminders(params.context):
                injected.append(reminder)
                yield reminder

        # 来自 inject_message() 的排队消息：每次迭代
        for queued_msg in _drain_message_queue(params.message_queue):
            injected.append(queued_msg)
            yield queued_msg

        if injected:
            state.messages = state.messages + injected

        # -- 阶段 2：消息准备（压缩管道） ----------
        messages_for_query = get_messages_after_compact_boundary(state.messages)

        # 从模型上下文窗口计算压缩阈值（已缓存）
        if state.cached_model_info is None:
            state.cached_model_info = params.provider.get_model_info(params.model)
        model_info = state.cached_model_info
        threshold_pct = params.settings.get("compaction_threshold_percent", 80) / 100.0
        threshold_tokens = int(model_info.context_window * threshold_pct)

        # 层 A：compaction_truncate_tool_results（截断超大结果）
        if params.settings.get("compaction_truncate_enabled", True):
            messages_for_query = compaction_truncate_tool_results(
                messages_for_query, threshold_tokens=threshold_tokens,
                settings=params.settings,
            )

        # 层 B：compaction_clear_tool_results（清除旧工具结果）
        if params.settings.get("compaction_clear_enabled", True):
            messages_for_query, tokens_saved = compaction_clear_tool_results(
                messages_for_query, threshold_tokens=threshold_tokens,
                settings=params.settings,
            )

        # 层 C：compaction_auto（如果仍超过阈值则自动压缩）。
        # 预调用 token 估算：
        # - 基于使用量 = 上次输入 + 上次输出 + 自上次调用以来添加的 token
        # - 完整估算 = 对完整请求负载使用 litellm.token_counter
        # 我们取最大值以确保永远不会预算不足。
        new_since_last = (
            state.messages[state.messages_len_at_last_call :]
            if state.last_input_tokens > 0
            else None
        )
        current_tokens = predicted_next_call_tokens(
            params.model,
            messages_for_query,
            system=params.system_prompt,
            tools=[t.to_schema() if hasattr(t, "to_schema") else t for t in params.tools],
            last_input_tokens=state.last_input_tokens,
            last_output_tokens=state.last_output_tokens,
            new_messages_since_last_call=new_since_last,
        )
        if params.settings.get("compaction_auto_enabled", True) and current_tokens >= threshold_tokens:
            # 检查断路器
            failures = (state.auto_compact_tracking or {}).get("consecutive_failures", 0)
            max_failures = params.settings.get("max_consecutive_compact_failures", 3)
            if failures >= max_failures:
                # 向用户显示错误并停止 —— 继续只会再次失败
                circuit_breaker_msg = create_user_message(
                    "Compaction has failed 3 times consecutively. Use /clear to start fresh.",
                    hide_in_ui=False,
                )
                yield circuit_breaker_msg
                return
            else:
                logger.info("Auto-compaction triggered (Layer C)")
                try:
                    result = await compaction_auto(
                        messages_for_query,
                        params.provider,
                        model=params.model,
                        memory_mode=params.memory_mode,
                        settings=params.settings,
                    )
                    if result:
                        # 生成压缩产物以便调用者可以显示/存储它们
                        yield result.boundary_message
                        for msg in result.summary_messages:
                            yield msg
                        messages_for_query = [result.boundary_message] + result.summary_messages
                        # 更新跟踪
                        state.auto_compact_tracking = {
                            "compacted": True,
                            "turn_counter": 0,
                            "consecutive_failures": 0,
                        }
                    else:
                        state.auto_compact_tracking = {
                            "compacted": False,
                            "turn_counter": 0,
                            "consecutive_failures": failures + 1,
                        }
                except Exception as e:
                    logger.warning("Auto-compact failed: %s", e)
                    state.auto_compact_tracking = {
                        "compacted": False,
                        "turn_counter": 0,
                        "consecutive_failures": failures + 1,
                    }

        # -- 阶段 3：阻塞限制检查 -------------------------------
        # 重用上面计算的相同保守估算。
        blocking_limit = model_info.context_window - params.settings.get("blocking_limit_buffer_tokens", 3000)
        if current_tokens >= blocking_limit:
            yield create_assistant_error_message(
                "Conversation too long. Please run /compact or start a new session."
            )
            return

        # -- 阶段 4：API 调用（流式传输） -------------------------------
        api_messages = normalize_messages_for_api(messages_for_query)
        api_messages_dicts = messages_to_openai_dicts(api_messages)

        # 通知 LLM 视角观察者（GUI）
        if params.llm_perspective_callback:
            params.llm_perspective_callback(api_messages_dicts, params.system_prompt)

        # 使用基于文本的工具调用时，不要将工具架构传递给提供者
        # 工具调用 —— 工具通过系统提示词进行通信。
        if params.settings.get("tool_call_format"):
            tool_schemas = []
        else:
            tool_schemas = [
                ToolSchema(**s) for s in tools_to_schemas(params.tools)
            ]

        max_tokens = (
            state.max_output_tokens_override
            or params.max_output_tokens
            or model_info.max_output_tokens
        )

        # 流式响应的累加器
        assistant_content: list[AssistantContentBlock] = []
        tool_use_blocks: list[ToolUseBlock] = []
        current_usage = Usage()
        current_model = params.model
        stop_reason: str | None = None
        request_id: str | None = None

        try:
            async for event in stream_with_retry(
                params.provider,
                api_messages_dicts,
                params.system_prompt,
                tool_schemas,
                model=params.model,
                max_tokens=max_tokens,
                system_static_boundary=params.system_static_boundary,
            ):
                # --- StreamMessageStart ---
                if isinstance(event, StreamMessageStart):
                    current_model = event.model
                    request_id = event.request_id
                    if event.usage:
                        current_usage = Usage(
                            **{
                                k: v
                                for k, v in event.usage.items()
                                if k in Usage.__dataclass_fields__
                            }
                        )

                # --- 文本增量 ---
                elif isinstance(event, StreamTextDelta):
                    if assistant_content and isinstance(assistant_content[-1], TextBlock):
                        assistant_content[-1].text += event.text
                    else:
                        assistant_content.append(TextBlock(text=event.text))
                    # 生成虚拟消息用于实时显示
                    yield AssistantMessage(
                        content=[TextBlock(text=event.text)],
                        model=current_model,
                        hide_in_api=True,
                    )

                # --- 工具使用生命周期 ---
                elif isinstance(event, StreamToolUseStart):
                    pass  # 通过 StreamToolUseStop 跟踪开始

                elif isinstance(event, StreamToolUseInputDelta):
                    pass  # 通过 StreamToolUseStop 跟踪部分 JSON

                elif isinstance(event, StreamToolUseStop):
                    block = ToolUseBlock(
                        id=event.id, name=event.name, input=event.input
                    )
                    assistant_content.append(block)
                    tool_use_blocks.append(block)

                # --- 思考增量 ---
                elif isinstance(event, StreamThinkingDelta):
                    if assistant_content and isinstance(assistant_content[-1], ThinkingBlock):
                        assistant_content[-1].thinking += event.thinking
                    else:
                        assistant_content.append(ThinkingBlock(thinking=event.thinking))
                    # 生成虚拟消息用于实时思考显示
                    yield AssistantMessage(
                        content=[ThinkingBlock(thinking=event.thinking)],
                        model=current_model,
                        hide_in_api=True,
                    )

                # --- 消息级元数据 ---
                elif isinstance(event, StreamMessageDelta):
                    stop_reason = event.stop_reason
                    if event.usage:
                        for k, v in event.usage.items():
                            if hasattr(current_usage, k):
                                setattr(
                                    current_usage,
                                    k,
                                    getattr(current_usage, k) + v,
                                )

                # --- 流错误 ---
                elif isinstance(event, StreamError):
                    yield create_assistant_error_message(
                        event.error, api_error=event.error_type
                    )
                    return

        except Exception as e:
            logger.error("Query error: %s", e)
            yield create_assistant_error_message(str(e))
            return

        # -- 阶段 5：构建最终助手消息 ----------------------
        # 修复思考模型：如果所有内容都在
        # ThinkingBlock 中且没有 TextBlock 存在，模型的答案嵌入在
        # 思考的末尾。将思考标记为响应。
        has_text = any(isinstance(b, TextBlock) and b.text.strip() for b in assistant_content)
        has_thinking = any(isinstance(b, ThinkingBlock) for b in assistant_content)
        if has_thinking and not has_text and not tool_use_blocks:
            # 思考就是响应 —— 作为文本添加注释
            logger.info("Thinking model returned empty content — using thinking as response")

        assistant_msg = AssistantMessage(
            content=assistant_content,
            model=current_model,
            stop_reason=stop_reason,
            usage=current_usage,
            request_id=request_id,
        )

        # -- 阶段 5.25：从文本中提取思考 -------------------------
        # 某些模型（例如通过 Ollama/LiteLLM 的 Qwen3 思考变体）将
        # <think>...</think> 嵌入在文本内容中，而不是使用单独的
        # 思考事件。将其提取为 ThinkingBlock。
        if not any(isinstance(b, ThinkingBlock) for b in assistant_content):
            full_text_for_thinking = "".join(
                b.text for b in assistant_content if isinstance(b, TextBlock)
            )
            if "<think>" in full_text_for_thinking or "</think>" in full_text_for_thinking:
                from pivotcode.tools.text_tool_parser import _extract_thinking
                thinking_text, remaining_text = _extract_thinking(full_text_for_thinking)
                if thinking_text:
                    new_blocks: list[AssistantContentBlock] = [
                        ThinkingBlock(thinking=thinking_text),
                    ]
                    if remaining_text:
                        new_blocks.append(TextBlock(text=remaining_text))
                    # 保留非文本块（tool_use 等）
                    for b in assistant_content:
                        if not isinstance(b, TextBlock):
                            new_blocks.append(b)
                    assistant_content = new_blocks
                    assistant_msg = AssistantMessage(
                        content=assistant_content,
                        model=current_model,
                        stop_reason=stop_reason,
                        usage=current_usage,
                        request_id=request_id,
                    )

        # -- 阶段 5.5：基于文本的工具调用提取 --------------------
        # 如果模型不支持原生工具调用，从文本输出中提取工具
        # 调用，使用配置的格式解析器。
        # 在格式错误的工具调用上，反馈错误并让模型重试。
        tool_call_format = params.settings.get("tool_call_format")
        if (
            tool_call_format
            and not tool_use_blocks
        ):
            full_text = "".join(
                b.text for b in assistant_content if isinstance(b, TextBlock)
            )
            if full_text:
                parse_result = extract_tool_calls_from_text(
                    full_text, format=tool_call_format,
                )

                if parse_result.tool_calls:
                    logger.info(
                        "Extracted %d tool call(s) from text (format=%s)",
                        len(parse_result.tool_calls), tool_call_format,
                    )
                    new_content: list[AssistantContentBlock] = []
                    if parse_result.thinking:
                        new_content.append(ThinkingBlock(thinking=parse_result.thinking))
                    if parse_result.cleaned_text:
                        new_content.append(TextBlock(text=parse_result.cleaned_text))
                    for pc in parse_result.tool_calls:
                        call_id = f"text_{uuid.uuid4().hex[:8]}"
                        block = ToolUseBlock(
                            id=call_id,
                            name=pc.name,
                            input=pc.input,
                        )
                        new_content.append(block)
                        tool_use_blocks.append(block)

                    assistant_msg = AssistantMessage(
                        content=new_content,
                        model=current_model,
                        stop_reason=stop_reason,
                        usage=current_usage,
                        request_id=request_id,
                    )

                elif parse_result.error:
                    # 模型尝试了工具调用但使用了错误的格式。
                    # 反馈错误并重试（最多 MAX_TEXT_TOOL_RETRIES 次）。
                    retry_count = getattr(state, "_text_tool_retries", 0)
                    if retry_count < MAX_TEXT_TOOL_RETRIES:
                        state._text_tool_retries = retry_count + 1  # type: ignore[attr-defined]
                        logger.warning(
                            "Malformed text tool call (retry %d/%d): %s",
                            retry_count + 1, MAX_TEXT_TOOL_RETRIES,
                            parse_result.error[:100],
                        )
                        # 生成格式错误的助手消息 + 错误反馈
                        yield assistant_msg
                        error_msg = create_user_message(
                            parse_result.error,
                            hide_in_ui=False,
                        )
                        yield error_msg
                        state.messages = list(messages_for_query) + [assistant_msg, error_msg]
                        state.transition = "text_tool_retry"
                        continue
                    else:
                        logger.error("Text tool call retries exhausted (%d)", MAX_TEXT_TOOL_RETRIES)

                elif parse_result.thinking or parse_result.cleaned_text != full_text:
                    # 没有工具调用但提取了思考或文本已更改 —— 重建
                    rebuilt_content: list[AssistantContentBlock] = []
                    if parse_result.thinking:
                        rebuilt_content.append(ThinkingBlock(thinking=parse_result.thinking))
                    if parse_result.cleaned_text:
                        rebuilt_content.append(TextBlock(text=parse_result.cleaned_text))
                    assistant_msg = AssistantMessage(
                        content=rebuilt_content,
                        model=current_model,
                        stop_reason=stop_reason,
                        usage=current_usage,
                        request_id=request_id,
                    )

        # 生成（可能已重建的）助手消息
        yield assistant_msg

        # 跟踪成本并记住上次调用的使用量，用于下一次迭代的
        # 预调用估算（参见 predicted_next_call_tokens）。
        params.cost_tracker.add_usage(current_usage, current_model)
        if current_usage.input_tokens > 0:
            state.last_input_tokens = current_usage.input_tokens
            state.last_output_tokens = current_usage.output_tokens
            state.messages_len_at_last_call = len(state.messages)

        # -- 阶段 6：流式传输后检查中止 ------------------------
        if params.abort_event and params.abort_event.is_set():
            yield create_user_interruption_message(tool_use=False)
            return

        # -- 阶段 7：处理无工具使用（完成或恢复） --------
        if not tool_use_blocks:
            # 最大输出 token 恢复
            if stop_reason == "max_tokens" or assistant_msg.api_error == "max_output_tokens":
                # 首先尝试升级（提升到 64K）
                if (
                    state.max_output_tokens_override is None
                    and not params.max_output_tokens
                ):
                    escalated = params.settings.get("escalated_max_tokens", 64000)
                    logger.info("Escalating max_tokens to %d", escalated)
                    state.max_output_tokens_override = escalated
                    state.messages = list(messages_for_query)
                    state.transition = "max_output_tokens_escalate"
                    continue

                # 多轮恢复
                if state.max_output_tokens_recovery_count < params.settings.get("max_output_tokens_recovery_limit", 3):
                    state.max_output_tokens_recovery_count += 1
                    recovery_msg = create_user_message(
                        "Output token limit hit. Resume directly -- no apology, no recap. "
                        "Pick up mid-thought. Break remaining work into smaller pieces.",
                        hide_in_ui=True,
                    )
                    state.messages = list(messages_for_query) + [
                        assistant_msg,
                        recovery_msg,
                    ]
                    state.max_output_tokens_override = None
                    state.transition = "max_output_tokens_recovery"
                    continue

            # 紧急压缩：检测 prompt 过长错误
            if (
                assistant_msg.is_api_error_message
                and assistant_msg.api_error
                and "prompt" in str(assistant_msg.api_error).lower()
                and "too long" in str(assistant_msg.api_error).lower()
                and not state.has_attempted_emergency_compact
            ):
                logger.info("Emergency compaction triggered (prompt too long)")
                try:
                    emergency_result = await compaction_auto(
                        messages_for_query,
                        params.provider,
                        model=params.model,
                        memory_mode=params.memory_mode,
                        settings=params.settings,
                    )
                    if emergency_result:
                        state.messages = (
                            [emergency_result.boundary_message]
                            + emergency_result.summary_messages
                        )
                        state.has_attempted_emergency_compact = True
                        state.transition = "emergency_compact_retry"
                        continue
                except Exception as e:
                    logger.warning("Emergency compaction failed: %s", e)

            # 正常完成
            return

        # -- 阶段 8：工具执行 -------------------------------------
        tool_results: list[UserMessage] = []

        async for update in run_tools(
            tool_use_blocks, params.tools, params.context,
            max_concurrency=params.settings.get("max_tool_concurrency", 10),
            permission_callback=params.permission_callback,
        ):
            if update.message:
                yield update.message
                tool_results.append(update.message)

        # 工具执行后检查中止
        if params.abort_event and params.abort_event.is_set():
            yield create_user_interruption_message(tool_use=True)
            return

        # -- 阶段 8.5：记忆提醒（密集模式） -----------------
        state.turns_since_memory_update += 1
        if (
            params.memory_mode == "intensive"
            and state.turns_since_memory_update >= params.settings.get("memory_reminder_threshold", 10)
        ):
            memory_reminder = create_user_message(
                "<system-reminder>\n"
                "Several turns have passed since the last memory update. "
                "Consider whether any recent corrections, decisions, or preferences "
                "are worth saving to memory.\n"
                "</system-reminder>",
                hide_in_ui=True,
            )
            tool_results.append(memory_reminder)
            yield memory_reminder
            state.turns_since_memory_update = 0

        # -- 阶段 9：检查最大轮次 ------------------------------------
        state.iteration_count += 1
        if params.max_iterations_per_turn and state.iteration_count >= params.max_iterations_per_turn:
            yield create_attachment_message(
                "max_iterations_per_turn_reached",
                metadata={
                    "max_iterations_per_turn": params.max_iterations_per_turn,
                    "iteration_count": state.iteration_count,
                },
            )
            return

        # -- 阶段 10：组装下一次迭代 ---------------------------
        state.messages = list(messages_for_query) + [assistant_msg] + tool_results
        state.max_output_tokens_recovery_count = 0
        state.max_output_tokens_override = None
        state.transition = "next_turn"
        iteration += 1
    # 结束 while True
