"""用于测试的脚本化 LLM 服务提供者。

``ScriptedProvider`` 是 Pivot Code 的单一测试提供者。它支持
两种操作模式：

**顺序模式** — 按 FIFO 顺序消费响应（简单测试）::

    provider = ScriptedProvider.from_responses([
        text("Hello!"),
        tool_call("Bash", {"command": "ls"}),
        text("Done."),
    ])

**响应式模式** — 通过检查对话的规则选择响应::

    provider = ScriptedProvider(rules=[
        rule(turn=0, respond=tool_call("Bash", {"command": "ls"})),
        rule(condition=lambda ctx: ctx.last_tool_result_contains("error"),
             respond=text("Something went wrong.")),
        rule(respond=text("Done.")),
    ])

两种模式可以通过 ``from_responses(..., fallback=...)`` 或
混合使用轮次索引规则和基于条件的规则来组合。
"""

from __future__ import annotations

import json
import uuid as _uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import Any

from pivotcode.providers.base import (
    LLMProvider,
    ModelInfo,
    ProviderStreamEvent,
    StreamError,
    StreamMessageDelta,
    StreamMessageStart,
    StreamMessageStop,
    StreamTextDelta,
    StreamToolUseInputDelta,
    StreamToolUseStart,
    StreamToolUseStop,
    ThinkingConfig,
    ToolSchema,
)

# ═══════════════════════════════════════════════════════════════════════════════
# 响应构建器
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ScriptedResponse:
    """服务提供者应该响应的内容。"""

    text: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    error: str | None = None
    stop_reason: str | None = None  # Auto-inferred if None
    usage: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.stop_reason is None:
            if self.tool_calls:
                self.stop_reason = "tool_use"
            elif self.error:
                self.stop_reason = "error"
            else:
                self.stop_reason = "end_turn"


def text(content: str) -> ScriptedResponse:
    """构建纯文本响应。"""
    return ScriptedResponse(text=content)


def tool_call(
    name: str, input: dict[str, Any], *, id: str | None = None
) -> ScriptedResponse:
    """构建单个工具调用响应。"""
    return ScriptedResponse(
        tool_calls=[{
            "name": name,
            "input": input,
            "id": id or f"toolu_{_uuid.uuid4().hex[:16]}",
        }]
    )


def multi_tool_call(*calls: tuple[str, dict[str, Any]]) -> ScriptedResponse:
    """构建包含多个工具调用的响应（如果为只读则并发执行）。"""
    return ScriptedResponse(
        tool_calls=[
            {"name": name, "input": inp, "id": f"toolu_{_uuid.uuid4().hex[:16]}"}
            for name, inp in calls
        ]
    )


def error(message: str) -> ScriptedResponse:
    """构建错误响应。"""
    return ScriptedResponse(error=message)


# ═══════════════════════════════════════════════════════════════════════════════
# 对话上下文 — 用于规则条件的结构化访问器
# ═══════════════════════════════════════════════════════════════════════════════


class ConversationContext:
    """对话的解析视图，用于编写规则条件。

    传递给条件函数，以便它们可以检查对话而无需手动解析消息字典。
    """

    def __init__(self, messages: list[dict[str, Any]], turn: int) -> None:
        self.messages = messages
        self.turn = turn

    # ── 工具结果 ──────────────────────────────────────────────────

    @property
    def last_tool_result(self) -> str:
        """最近工具结果的文本（如果没有则为空字符串）。"""
        for msg in reversed(self.messages):
            # OpenAI 格式
            if msg.get("role") == "tool":
                return msg.get("content", "")
            # Anthropic 格式
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        c = block.get("content", "")
                        return c if isinstance(c, str) else str(c)
        return ""

    def last_tool_result_contains(self, substring: str) -> bool:
        """检查最后一个工具结果是否包含子字符串（不区分大小写）。"""
        return substring.lower() in self.last_tool_result.lower()

    @property
    def last_tool_result_is_error(self) -> bool:
        """检查最后一个工具结果是否为错误。"""
        for msg in reversed(self.messages):
            if msg.get("role") == "tool":
                # 无法可靠地检测 OpenAI 格式中的错误
                return "error" in msg.get("content", "").lower()[:100]
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        return block.get("is_error", False)
        return False

    # ── 工具调用 ────────────────────────────────────────────────────

    def tool_was_called(self, tool_name: str) -> bool:
        """检查对话中是否调用了特定工具。"""
        for msg in self.messages:
            # Anthropic 格式
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        if block.get("name") == tool_name:
                            return True
            # OpenAI 格式
            for tc in msg.get("tool_calls", []):
                if isinstance(tc, dict):
                    fn = tc.get("function", {})
                    if fn.get("name") == tool_name:
                        return True
        return False

    def tool_call_count(self, tool_name: str | None = None) -> int:
        """计算工具被调用的次数（如果 name 为 None 则计算所有工具）。"""
        count = 0
        for msg in self.messages:
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        if tool_name is None or block.get("name") == tool_name:
                            count += 1
            for tc in msg.get("tool_calls", []):
                if isinstance(tc, dict):
                    fn = tc.get("function", {})
                    if tool_name is None or fn.get("name") == tool_name:
                        count += 1
        return count

    # ── 用户消息 ─────────────────────────────────────────────────

    @property
    def last_user_text(self) -> str:
        """最后一条用户消息的文本。"""
        for msg in reversed(self.messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = [
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    return " ".join(parts)
        return ""

    # ── 消息计数 ────────────────────────────────────────────────

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def assistant_message_count(self) -> int:
        return sum(1 for m in self.messages if m.get("role") == "assistant")


# ═══════════════════════════════════════════════════════════════════════════════
# 规则系统
# ═══════════════════════════════════════════════════════════════════════════════

# 条件函数接收 ConversationContext → bool
ConditionFn = Callable[[ConversationContext], bool]


@dataclass
class Rule:
    """条件响应规则。

    规则按顺序评估；第一个匹配的规则生效。当规则的
    所有条件都满足时匹配：

    - ``turn`` — 匹配特定轮次编号（0 索引的 API 调用计数）
    - ``condition`` — 接收 ``ConversationContext`` 的可调用对象
    - 如果两者都未设置，规则始终匹配（默认回退）。
    """

    respond: ScriptedResponse
    turn: int | None = None
    condition: ConditionFn | None = None

    def matches(self, ctx: ConversationContext) -> bool:
        if self.turn is not None and self.turn != ctx.turn:
            return False
        if self.condition is not None and not self.condition(ctx):
            return False
        return True


def rule(
    respond: ScriptedResponse,
    *,
    turn: int | None = None,
    condition: ConditionFn | None = None,
) -> Rule:
    """创建响应规则。"""
    return Rule(respond=respond, turn=turn, condition=condition)


# ═══════════════════════════════════════════════════════════════════════════════
# 服务提供者
# ═══════════════════════════════════════════════════════════════════════════════


class ScriptedProvider(LLMProvider):
    """具有脚本化响应的测试提供者 — 顺序或响应式。

    每次对 ``stream()`` 的调用都记录在 ``call_log`` 中以用于断言。
    """

    def __init__(self, rules: list[Rule] | None = None) -> None:
        self._rules: list[Rule] = list(rules or [])
        self._call_count: int = 0
        self.call_log: list[dict[str, Any]] = []

    # ── 工厂方法 ───────────────────────────────────────────────

    @classmethod
    def from_responses(
        cls,
        responses: list[ScriptedResponse],
        *,
        fallback: ScriptedResponse | None = None,
    ) -> ScriptedProvider:
        """创建具有 FIFO 响应（和可选回退）的提供者。

        每个响应按顺序消费。如果提供了回退，
        则在队列耗尽时使用。否则，产生错误。
        """
        rules = [Rule(respond=r, turn=i) for i, r in enumerate(responses)]
        if fallback is not None:
            rules.append(Rule(respond=fallback))
        return cls(rules=rules)

    # ── 便捷辅助方法 ───────────────────────────────────────────

    def add_rule(self, r: Rule) -> None:
        """添加规则。"""
        self._rules.append(r)

    # ── LLMProvider 接口 ─────────────────────────────────────────

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: list[str],
        tools: list[ToolSchema],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        thinking: ThinkingConfig | None = None,
        stop_sequences: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ProviderStreamEvent, None]:
        """评估规则并产生匹配的响应。"""

        turn = self._call_count
        self._call_count += 1

        self.call_log.append({
            "messages": messages,
            "system": system,
            "tools": tools,
            "model": model,
            "turn": turn,
        })

        # 为规则评估构建对话上下文
        ctx = ConversationContext(messages, turn)

        # 查找第一个匹配的规则
        resp: ScriptedResponse | None = None
        for r in self._rules:
            if r.matches(ctx):
                resp = r.respond
                break

        if resp is None:
            yield StreamError(
                error="ScriptedProvider: no matching rule for this conversation state",
                error_type="api_error",
            )
            return

        effective_model = model or "scripted-model"

        # 错误响应
        if resp.error is not None:
            yield StreamError(error=resp.error, error_type="api_error")
            return

        # 正常响应
        yield StreamMessageStart(
            model=effective_model,
            request_id=f"scripted-req-{turn}",
        )

        if resp.text is not None:
            yield StreamTextDelta(text=resp.text)

        if resp.tool_calls:
            for tc in resp.tool_calls:
                tool_id = tc.get("id", f"toolu_{_uuid.uuid4().hex[:16]}")
                tool_name = tc["name"]
                tool_input = tc["input"]

                yield StreamToolUseStart(id=tool_id, name=tool_name)
                yield StreamToolUseInputDelta(
                    id=tool_id, partial_json=json.dumps(tool_input)
                )
                yield StreamToolUseStop(
                    id=tool_id, name=tool_name, input=tool_input
                )

        yield StreamMessageDelta(
            stop_reason=resp.stop_reason,
            usage=resp.usage or {"input_tokens": 100, "output_tokens": 50},
        )
        yield StreamMessageStop()

    def get_model_info(self, model: str | None = None) -> ModelInfo:
        return ModelInfo(
            context_window=200_000,
            max_output_tokens=8_192,
        )

