"""SessionUI —— 所有会话输入输出的抽象接口。

CLI 与 GUI 均实现此接口。会话循环（``run_session``）
与具体 UI 无关：无论使用哪种实现，其行为完全一致。

斜杠命令会收到 ``ui.console``（一个 Rich Console 或 GUIConsole）并调用
``console.print()`` —— 斜杠命令代码无需做任何改动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console

    from pivotcode.messages.types import Message, StreamEvent, Usage


class SessionUI(ABC):
    """会话输入输出的抽象接口。

    实现包括：:class:`CLIUI`（终端）与 :class:`GUIUI`（浏览器）。
    """

    # 如果该 UI 自行重渲染整个会话（例如通过
    # ``on_initial_conversation``），则 REPL 可跳过文本形式的
    # "会话已恢复 / 上次对话" 概要，避免信息重复。
    renders_conversation: bool = False

    # ── Input ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_input(self, prompt: str = "\n> ") -> str:
        """等待用户输入（主提示或自由文本）。

        返回用户输入的文本（已去除首尾空白）。
        在 Ctrl+D / 断开连接时抛出 ``EOFError``。
        """
        ...

    @abstractmethod
    async def ask_user(self, question: str, options: list[str]) -> str:
        """向用户提出带选项的问题（权限确认、AskUserQuestion 等）。

        返回所选选项文本或用户自定义输入。
        """
        ...

    # ── Agent event output ────────────────────────────────────────────────

    @abstractmethod
    async def on_agent_event(self, event: StreamEvent | Message) -> None:
        """展示来自智能体的事件（流式增量、工具调用等）。"""
        ...

    @abstractmethod
    async def on_cost(
        self,
        usage: Usage,
        cost_usd: float,
        cost_unknown: bool,
        conversation_tokens: int = 0,
        context_window: int = 0,
    ) -> None:
        """在一轮对话结束后展示成本概要。

        Parameters
        ----------
        usage : Usage
            累计的会话 token 用量。
        cost_usd : float
            累计的预估成本。
        cost_unknown : bool
            当价格信息不可用时为 True。
        conversation_tokens : int
            当前会话按预估 token 计的大小。
        context_window : int
            模型的上下文窗口大小。
        """
        ...

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def on_agent_start(self) -> None:
        """当智能体开始处理一轮对话时调用。

        GUI 借此禁用输入并显示停止按钮。
        CLI 中为空操作。
        """

    def on_agent_done(self) -> None:
        """当智能体完成一轮对话处理时调用。

        GUI 借此重新启用输入。
        CLI 中为空操作。
        """

    def reset_stream_state(self, assume_thinking: bool = False) -> None:
        """在新一轮对话开始前重置流式显示状态。

        CLI 重置 ``<think>``/``<tool_call>`` 标签过滤状态机。
        GUI 中为空操作。
        """

    # ── Initial data (sent once at session start) ─────────────────────────

    def on_initial_conversation(self, messages: list) -> None:
        """在会话开始时将已有的会话消息发送给 UI。

        GUI 会在聊天面板中渲染它们。CLI 中为空操作（已显示在屏幕上）。
        """

    def on_initial_system_prompt(self, system_prompt: str) -> None:
        """在会话开始时将系统提示发送给 LLM Perspective 面板。

        GUI 会展示它。CLI 中为空操作。
        """

    # ── Git Tree (AGT) ──────────────────────────────────────────────────────

    def on_git_tree_update(self, tree_data: dict) -> None:
        """当 git 树布局需要刷新时调用。

        GUI 通过 WebSocket 将数据发送给浏览器。
        CLI 与 ScriptedUI 中均为空操作。
        """

    # ── Console (for slash commands) ──────────────────────────────────────

    @property
    @abstractmethod
    def console(self) -> Console:
        """供斜杠命令用于输出的 Rich Console。

        CLI：真实的 Rich Console（美观的表格、语法高亮）。
        GUI：GUIConsole（渲染为文本，经 WebSocket 发送）。
        """
        ...
