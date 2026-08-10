"""ScriptedUI —— 带脚本输入的测试用 SessionUI 实现。

``ScriptedUI`` 借鉴了 ``ScriptedProvider`` 的模式：它支持
顺序与响应式两种模式，用于在测试中编排用户交互。

**顺序模式** —— 输入按 FIFO 顺序消费::

    ui = ScriptedUI.from_inputs(["Fix the bug", "yes", "/exit"])

**响应式模式** —— 由检查事件日志的规则来选择输入::

    ui = ScriptedUI(rules=[
        ui_rule(turn=0, respond="Fix the bug"),
        ui_rule(
            input_type="ask",
            condition=lambda ctx: "permission" in ctx.last_question.lower(),
            respond="yes",
        ),
        ui_rule(respond="/exit"),
    ])

两种模式可以组合使用。所有事件与交互都会被记录，
以便测试中做断言。
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

from pivotcode.gui.base import SessionUI
from pivotcode.messages.types import Message, StreamEvent, Usage

# ═══════════════════════════════════════════════════════════════════════════════
# 上下文 —— 供规则条件使用的结构化访问器
# ═══════════════════════════════════════════════════════════════════════════════


class UIContext:
    """用于编写规则条件的 UI 状态的解析视图。

    它会被传给条件函数，使它们无需手动解析日志即可检查
    会话中发生过什么。
    """

    def __init__(
        self,
        event_log: list[dict[str, Any]],
        input_log: list[dict[str, Any]],
        console_log: list[str],
        input_count: int,
        current_prompt: str,
        current_question: str,
        current_options: list[str],
    ) -> None:
        self.event_log = event_log
        self.input_log = input_log
        self.console_log = console_log
        self.input_count = input_count
        self.current_prompt = current_prompt
        self.current_question = current_question
        self.current_options = current_options

    @property
    def last_question(self) -> str:
        """来自当前或最近一次 ask_user 调用的问题。"""
        return self.current_question

    @property
    def last_prompt(self) -> str:
        """来自当前 get_input 调用的提示。"""
        return self.current_prompt

    @property
    def event_count(self) -> int:
        return len(self.event_log)

    @property
    def last_event_type(self) -> str:
        """最近一次事件的类型字符串（若无则为空）。"""
        if not self.event_log:
            return ""
        return self.event_log[-1].get("type", "")

    @property
    def last_console_output(self) -> str:
        """最近一次 console.print() 的输出（若无则为空）。"""
        return self.console_log[-1] if self.console_log else ""

    def console_output_contains(self, substring: str) -> bool:
        """检查是否有任意控制台输出包含某子串。"""
        return any(substring in line for line in self.console_log)

    def event_type_count(self, event_type: str) -> int:
        """统计特定类型的事件数量。"""
        return sum(1 for e in self.event_log if e.get("type") == event_type)


# ═══════════════════════════════════════════════════════════════════════════════
# 规则系统
# ═══════════════════════════════════════════════════════════════════════════════


UIConditionFn = Callable[[UIContext], bool]


@dataclass
class UIRule:
    """一条带条件的输入规则。

    规则按顺序求值；第一个匹配者胜出。当规则的所有条件都满足时即匹配：

    - ``turn`` —— 匹配特定的输入计数（从 0 开始）
    - ``input_type`` —— 匹配输入请求的类型（``"prompt"`` 或 ``"ask"``）
    - ``condition`` —— 接收 ``UIContext`` 的可调用对象
    - 若均未设置，则该规则始终匹配（默认兜底）。

    特殊响应：
    - ``respond=EOFError`` —— 抛出 EOFError（模拟 Ctrl+D / 会话结束）
    """

    respond: str | type
    turn: int | None = None
    input_type: str | None = None  # "prompt" 或 "ask"
    condition: UIConditionFn | None = None
    _consumed: bool = field(default=False, repr=False)

    def matches(self, ctx: UIContext, request_type: str) -> bool:
        if self.turn is not None and self.turn != ctx.input_count:
            return False
        if self.input_type is not None and self.input_type != request_type:
            return False
        if self.condition is not None and not self.condition(ctx):
            return False
        return True


def ui_rule(
    respond: str | type,
    *,
    turn: int | None = None,
    input_type: str | None = None,
    condition: UIConditionFn | None = None,
) -> UIRule:
    """创建一条 UI 输入规则。"""
    return UIRule(respond=respond, turn=turn, input_type=input_type, condition=condition)


# ═══════════════════════════════════════════════════════════════════════════════
# ScriptedUI
# ═══════════════════════════════════════════════════════════════════════════════


class ScriptedUI(SessionUI):
    """带脚本输入的测试 UI —— 顺序或响应式。

    所有事件、输入以及控制台输出都会被记录，以供断言。

    Parameters
    ----------
    rules : list[UIRule], optional
        用于确定输入响应的规则。
    """

    def __init__(self, rules: list[UIRule] | None = None) -> None:
        self._rules: list[UIRule] = list(rules or [])
        self._input_count: int = 0

        # ── 日志（用于测试断言） ───────────────────────────────
        self.event_log: list[dict[str, Any]] = []
        self.input_log: list[dict[str, Any]] = []
        self.cost_log: list[dict[str, Any]] = []
        self.console_log: list[str] = []
        self.lifecycle_log: list[str] = []  # "agent_start"、"agent_done"、"reset_stream"
        self.tree_update_log: list[dict[str, Any]] = []  # AGT 树更新

        # ── 控制台 ──────────────────────────────────────────────────
        self._buf = io.StringIO()
        self._console = _ScriptedConsole(self)

    # ── 工厂方法 ──────────────────────────────────────────────

    @classmethod
    def from_inputs(
        cls,
        inputs: list[str | type],
        *,
        fallback: str | type | None = None,
    ) -> ScriptedUI:
        """创建一个带 FIFO 输入（以及可选兜底值）的 UI。

        每个输入按序消费。使用 ``EOFError`` 来表示会话结束。
        若提供了兜底值，则在队列耗尽时使用它。

        Example::

            ui = ScriptedUI.from_inputs([
                "Fix the bug in main.py",
                "yes",
                EOFError,  # 结束会话
            ])
        """
        rules = [UIRule(respond=r, turn=i) for i, r in enumerate(inputs)]
        if fallback is not None:
            rules.append(UIRule(respond=fallback))
        return cls(rules=rules)

    # ── 便捷方法 ──────────────────────────────────────────────────

    def add_rule(self, r: UIRule) -> None:
        """追加一条规则。"""
        self._rules.append(r)

    # ── SessionUI：输入 ─────────────────────────────────────────────

    async def get_input(self, prompt: str = "\n> ") -> str:
        """返回下一个脚本化输入，或抛出 EOFError。"""
        response = self._resolve("prompt", prompt=prompt, question="", options=[])
        self.input_log.append({
            "type": "prompt",
            "prompt": prompt,
            "response": str(response) if response is not EOFError else "<<EOF>>",
            "turn": self._input_count - 1,
        })
        if response is EOFError:
            raise EOFError("ScriptedUI: end of inputs")
        return response

    async def ask_user(self, question: str, options: list[str]) -> str:
        """返回对某个问题的下一个脚本化回答。"""
        response = self._resolve("ask", prompt="", question=question, options=options)
        self.input_log.append({
            "type": "ask",
            "question": question,
            "options": options,
            "response": str(response) if response is not EOFError else "<<EOF>>",
            "turn": self._input_count - 1,
        })
        if response is EOFError:
            raise EOFError("ScriptedUI: end of inputs (ask)")
        return response

    def _resolve(
        self,
        request_type: str,
        *,
        prompt: str,
        question: str,
        options: list[str],
    ) -> str | type:
        """查找匹配的规则并返回响应。"""
        ctx = UIContext(
            event_log=self.event_log,
            input_log=self.input_log,
            console_log=self.console_log,
            input_count=self._input_count,
            current_prompt=prompt,
            current_question=question,
            current_options=options,
        )

        self._input_count += 1

        for r in self._rules:
            if r.matches(ctx, request_type):
                return r.respond

        # 没有匹配的规则 —— 结束会话
        return EOFError

    # ── SessionUI：智能体事件输出 ────────────────────────────────

    async def on_agent_event(self, event: StreamEvent | Message) -> None:
        """记录该事件，以便后续断言。"""
        entry: dict[str, Any] = {"type": type(event).__name__}
        # 提取便于断言的字段
        if hasattr(event, "text"):
            entry["text"] = event.text
        if hasattr(event, "content"):
            entry["content"] = event.content
        if hasattr(event, "stop_reason"):
            entry["stop_reason"] = event.stop_reason
        if hasattr(event, "model"):
            entry["model"] = event.model
        self.event_log.append(entry)

    async def on_cost(
        self,
        usage: Usage,
        cost_usd: float,
        cost_unknown: bool,
        conversation_tokens: int = 0,
        context_window: int = 0,
    ) -> None:
        """记录成本信息。"""
        self.cost_log.append({
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": cost_usd,
            "cost_unknown": cost_unknown,
            "conversation_tokens": conversation_tokens,
            "context_window": context_window,
        })

    # ── SessionUI：生命周期 ─────────────────────────────────────────

    def on_agent_start(self) -> None:
        self.lifecycle_log.append("agent_start")

    def on_agent_done(self) -> None:
        self.lifecycle_log.append("agent_done")

    def reset_stream_state(self, assume_thinking: bool = False) -> None:
        self.lifecycle_log.append(f"reset_stream(thinking={assume_thinking})")

    # ── SessionUI：Git 树 ──────────────────────────────────────────

    def on_git_tree_update(self, tree_data: dict) -> None:
        """记录树更新，以便测试断言。"""
        self.tree_update_log.append(tree_data)

    # ── SessionUI：控制台 ───────────────────────────────────────────

    @property
    def console(self) -> Console:
        return self._console

    # ── 断言辅助 ────────────────────────────────────────────

    @property
    def prompt_responses(self) -> list[str]:
        """对 get_input() 调用的所有响应。"""
        return [
            e["response"] for e in self.input_log if e["type"] == "prompt"
        ]

    @property
    def ask_responses(self) -> list[str]:
        """对 ask_user() 调用的所有响应。"""
        return [
            e["response"] for e in self.input_log if e["type"] == "ask"
        ]

    @property
    def events_by_type(self) -> dict[str, list[dict[str, Any]]]:
        """按类型名称分组的事件。"""
        result: dict[str, list[dict[str, Any]]] = {}
        for e in self.event_log:
            result.setdefault(e["type"], []).append(e)
        return result


# ═══════════════════════════════════════════════════════════════════════════════
# _ScriptedConsole —— 捕获 console.print() 的输出
# ═══════════════════════════════════════════════════════════════════════════════


class _ScriptedConsole(Console):
    """用于测试断言而捕获输出的 Rich Console 子类。

    不会写入终端。
    """

    def __init__(self, scripted_ui: ScriptedUI) -> None:
        self._inner_buf = io.StringIO()
        super().__init__(file=self._inner_buf, width=120, no_color=True)
        self._scripted_ui = scripted_ui

    def print(self, *objects: Any, **kwargs: Any) -> None:  # type: ignore[override]
        self._inner_buf.truncate(0)
        self._inner_buf.seek(0)
        super().print(*objects, **kwargs)
        text = self._inner_buf.getvalue().rstrip()
        self._inner_buf.truncate(0)
        self._inner_buf.seek(0)
        if text:
            self._scripted_ui.console_log.append(text)
