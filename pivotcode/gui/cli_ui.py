"""CLIUI —— 基于终端的 SessionUI 实现。

使用 Rich 进行显示，prompt_toolkit 进行输入。
当未传入 ``--gui`` 时，这是默认的 UI。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style as PTStyle
from rich.console import Console

from pivotcode.cli.display import (
    _reset_stream_state,
    display_event,
    display_replay,
)
from pivotcode.gui.base import SessionUI
from pivotcode.messages.types import Message, StreamEvent, Usage


class CLIUI(SessionUI):
    """终端 UI：Rich 控制台 + prompt_toolkit 输入。"""

    # CLI 现在会在恢复会话时重放对话尾部
    # （见下方的 on_initial_conversation），因此 REPL 可跳过
    # "上次对话" 文本概要。
    renders_conversation = True

    def __init__(self) -> None:
        self._console = Console()

        # 配置带持久化历史的 prompt-toolkit。
        # 回车 = 提交，Alt+Enter（先按 Esc 再按回车）= 换行。
        from prompt_toolkit.key_binding import KeyBindings

        kb = KeyBindings()

        @kb.add("escape", "enter")
        def _insert_newline(event):
            event.current_buffer.insert_text("\n")

        history_path = Path.home() / ".pivot" / "history"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        self._session: PromptSession[str] = PromptSession(
            history=FileHistory(str(history_path)),
            key_bindings=kb,
        )
        # 用于权限提示的独立会话 —— 无历史、无自定义按键绑定，
        # 但使用相同的 prompt_toolkit 机制，以便 Ctrl+C 能干净地退出，
        # 而不会在 stdin 上遗留一个被阻塞的 input()。
        self._ask_session: PromptSession[str] = PromptSession()

    # ── Input ─────────────────────────────────────────────────────────────

    _INPUT_STYLE = PTStyle.from_dict({"": "ansibrightblack"})

    async def get_input(self, prompt: str = "\n> ") -> str:
        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(
            None, lambda: self._session.prompt(prompt, style=self._INPUT_STYLE)
        )
        return text.strip()

    async def ask_user(self, question: str, options: list[str]) -> str:
        from pivotcode.cli.user_input import ask_user_cli

        return await ask_user_cli(question, options, session=self._ask_session)

    # ── Agent event output ────────────────────────────────────────────────

    def on_initial_conversation(self, messages: list) -> None:
        """将恢复会话的对话尾部重放到终端。"""
        display_replay(messages, self._console, limit=100)

    async def on_agent_event(self, event: StreamEvent | Message) -> None:
        display_event(event, self._console)

    async def on_cost(
        self, usage: Usage, cost_usd: float, cost_unknown: bool,
        conversation_tokens: int = 0, context_window: int = 0,
    ) -> None:
        # 会话成本。若整个会话期间都未上报缓存 token，则当服务商启用了
        # prompt 缓存但未向我们暴露明细时，该数值可能偏高。
        parts = [f"  [dim]Session: {usage.total_input:,} in + {usage.output_tokens:,} out"]
        if not cost_unknown:
            no_cache_reported = (
                usage.cache_creation_input_tokens == 0
                and usage.cache_read_input_tokens == 0
            )
            label = "estimate w/o cache" if no_cache_reported else "estimated"
            parts.append(f"= ${cost_usd:.4f} ({label})")
        # 会话 token 数
        if context_window > 0 and conversation_tokens > 0:
            pct = conversation_tokens * 100 // context_window
            parts.append(
                f"| Conversation: {conversation_tokens:,} / {context_window:,} ({pct}%)"
            )
        self._console.print(" ".join(parts) + "[/dim]")

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def on_agent_start(self) -> None:
        # 在用户输入与助手回复之间留一个空行，便于阅读。
        self._console.print()

    def reset_stream_state(self, assume_thinking: bool = False) -> None:
        _reset_stream_state(assume_thinking=assume_thinking)

    # ── Console ───────────────────────────────────────────────────────────

    @property
    def console(self) -> Console:
        return self._console
