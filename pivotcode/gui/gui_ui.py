"""GUIUI —— 基于浏览器的 SessionUI 实现。

运行一个 FastAPI/uvicorn 服务器并通过 WebSocket 与浏览器通信。
所有输入输出都经由浏览器 —— 没有终端输入。

用法::

    ui = GUIUI(agent, cwd="/path/to/project")
    await ui.start()       # 启动服务器，打印 URL
    await run_session(agent, ui)
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import TYPE_CHECKING, Any

from rich.console import Console

from pivotcode.gui.base import SessionUI
from pivotcode.gui.serialization import agent_event_to_output

if TYPE_CHECKING:
    from fastapi import WebSocket

    from pivotcode.agent import PivotCodeAgent
    from pivotcode.messages.types import Message, StreamEvent, Usage

logger = logging.getLogger(__name__)


class GUIUI(SessionUI):
    """浏览器 GUI：所有输入输出均经由 WebSocket。

    Parameters
    ----------
    agent : PivotCodeAgent
        智能体（用于会话信息、中止操作、LLM 视角）。
    cwd : str
        工作目录（用于 URL 路径与会话列表）。
    """

    # 聊天面板在恢复会话时会自行渲染整个对话。
    renders_conversation = True

    def __init__(
        self,
        agent: PivotCodeAgent,
        cwd: str = "",
        *,
        gui_label: str | None = None,
    ) -> None:
        self._agent = agent
        self._cwd = cwd
        # URL 路径段的备选覆盖值。为 None 时，会使用智能体的
        # gui_label（若有）；若也为 None，则服务器回退到
        # ``Path(cwd).name``。
        self._gui_label = gui_label or getattr(agent, "_gui_label", None)
        self._connections: set[WebSocket] = set()
        self._pending_input: asyncio.Future[str] | None = None
        self._event_history: list[dict] = []  # 用于重连时重放
        self._console_instance = _GUIConsole(self)
        self.llm_perspective: list[dict] | None = None
        self.llm_system_prompt: str = ""
        self._last_tree_data: dict | None = None

    # ── Server lifecycle ──────────────────────────────────────────────────

    async def start(self, port: int | None = None) -> str:
        """启动 FastAPI 服务器。返回 URL。"""
        from pivotcode.gui.server import start_gui_server

        url, self._server, self._server_task = await start_gui_server(
            gui_ui=self, cwd=self._cwd, port=port, label=self._gui_label,
        )
        return url

    async def stop(self) -> None:
        """请求 uvicorn 服务器干净地关闭。"""
        server = getattr(self, "_server", None)
        task = getattr(self, "_server_task", None)
        if server is None or task is None:
            return
        # 关闭所有活跃的 websocket，以便 uvicorn 的优雅关闭能够
        # 立即完成，而不必等待浏览器断开连接。
        for ws in list(self._connections):
            try:
                await ws.close()
            except Exception:
                pass
        self._connections.clear()
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except TimeoutError:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        except (asyncio.CancelledError, Exception):
            pass

    # ── WebSocket connection management ───────────────────────────────────

    def add_connection(self, ws: WebSocket) -> None:
        self._connections.add(ws)
        logger.info("GUI client connected (%d total)", len(self._connections))

    def remove_connection(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        logger.info("GUI client disconnected (%d total)", len(self._connections))

    async def send_to_all(self, msg: str) -> None:
        """向所有已连接的浏览器发送一条文本消息。"""
        dead: set[WebSocket] = set()
        for ws in self._connections:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        self._connections -= dead

    async def _send_event(self, event_type: str, data: dict) -> None:
        """向所有浏览器发送一个结构化事件。"""
        msg = json.dumps({
            "kind": "event",
            "event": {"type": event_type, "data": data},
        }, default=str)
        self._event_history.append(json.loads(msg))
        await self.send_to_all(msg)

    # ── Replay (for clients connecting mid-session) ───────────────────────

    async def send_history(self, ws: WebSocket) -> None:
        """向新连接的客户端发送完整的事件历史。

        以 ``reset`` 事件开头，使浏览器在重新渲染前丢弃它从上一个
        （现已失效的）会话保留下来的任何 DOM —— 这正是避免 ``pivotcode``
        重启时消息重复的关键。
        """
        await ws.send_text(json.dumps({
            "kind": "event",
            "event": {"type": "reset", "data": {}},
        }))
        for entry in self._event_history:
            await ws.send_text(json.dumps(entry, default=str))

        # 若 LLM 视角可用则发送
        if self.llm_perspective:
            await ws.send_text(json.dumps({
                "kind": "event",
                "event": {
                    "type": "llm_perspective",
                    "data": {
                        "messages": self.llm_perspective,
                        "system_prompt": self.llm_system_prompt,
                    },
                },
            }, default=str))

        # 若 git 树数据可用则发送
        if self._last_tree_data:
            await ws.send_text(json.dumps({
                "kind": "event",
                "event": {
                    "type": "git_tree_update",
                    "data": self._last_tree_data,
                },
            }, default=str))

        # 若当前正在等待输入，则通知新客户端
        if self._pending_input and not self._pending_input.done():
            await ws.send_text(json.dumps({
                "kind": "input_request",
                "request": {"type": "prompt", "question": "> ", "options": []},
            }))

    # ── SessionUI: Input ──────────────────────────────────────────────────

    # 在放弃输入前等待浏览器连接的时长。
    # 既要足够长以便有人能打开 URL；又要足够短，以免在防火墙关闭或
    # URL 错误时无限期挂起。
    BROWSER_CONNECT_TIMEOUT = 120.0

    async def _wait_for_browser_or_fail(self) -> None:
        """阻塞直到至少有一个浏览器连接，或超时。

        超时会抛出带有可操作指引的 ``TimeoutError``，这样调用方可以
        将其呈现给用户，而不是永远挂起。
        """
        if self._connections:
            return
        start = asyncio.get_running_loop().time()
        while not self._connections:
            if asyncio.get_running_loop().time() - start > self.BROWSER_CONNECT_TIMEOUT:
                raise TimeoutError(
                    "No browser connected after "
                    f"{int(self.BROWSER_CONNECT_TIMEOUT)}s. Open the GUI "
                    "URL printed at startup, or run `pivotcode` without "
                    "`--gui` for the terminal UI."
                )
            await asyncio.sleep(0.1)

    async def get_input(self, prompt: str = "\n> ") -> str:
        """等待来自浏览器的用户输入。"""
        await self._wait_for_browser_or_fail()

        await self.send_to_all(json.dumps({
            "kind": "input_request",
            "request": {"type": "prompt", "question": prompt, "options": []},
        }))

        loop = asyncio.get_running_loop()
        self._pending_input = loop.create_future()
        try:
            return await self._pending_input
        finally:
            self._pending_input = None

    async def ask_user(self, question: str, options: list[str]) -> str:
        """通过浏览器向用户提出带选项的问题。"""
        await self._wait_for_browser_or_fail()

        await self.send_to_all(json.dumps({
            "kind": "input_request",
            "request": {
                "type": "ask",
                "question": question,
                "options": options,
            },
        }))

        loop = asyncio.get_running_loop()
        self._pending_input = loop.create_future()
        try:
            return await self._pending_input
        finally:
            self._pending_input = None

    def submit_input(self, value: str) -> bool:
        """当浏览器发送输入时，由 WebSocket 处理器调用。"""
        if self._pending_input is None or self._pending_input.done():
            return False
        self._pending_input.set_result(value)
        return True

    # ── SessionUI: Agent event output ─────────────────────────────────────

    async def on_agent_event(self, event: StreamEvent | Message) -> None:
        output = agent_event_to_output(event)
        await self._send_event(output.type, output.data)

    async def on_cost(
        self, usage: Usage, cost_usd: float, cost_unknown: bool,
        conversation_tokens: int = 0, context_window: int = 0,
    ) -> None:
        await self._send_event("cost_summary", {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_input_tokens,
            "cache_write_tokens": usage.cache_creation_input_tokens,
            "cost_usd": cost_usd,
            "cost_unknown": cost_unknown,
            "conversation_tokens": conversation_tokens,
            "context_window": context_window,
        })

    # ── SessionUI: Lifecycle ──────────────────────────────────────────────

    def on_agent_start(self) -> None:
        asyncio.ensure_future(self.send_to_all(json.dumps({"kind": "agent_start"})))

    def on_agent_done(self) -> None:
        asyncio.ensure_future(self.send_to_all(json.dumps({"kind": "agent_done"})))

    def reset_stream_state(self, assume_thinking: bool = False) -> None:
        pass  # GUI 没有流式状态机

    # ── SessionUI: Console ────────────────────────────────────────────────

    @property
    def console(self) -> Console:
        return self._console_instance

    # ── LLM Perspective ───────────────────────────────────────────────────

    def set_llm_perspective(
        self,
        api_messages: list[dict],
        system_prompt: list[str] | None = None,
    ) -> None:
        """存储 LLM 视角并发送给浏览器。"""
        self.llm_perspective = api_messages
        self.llm_system_prompt = "\n\n".join(system_prompt) if system_prompt else ""
        asyncio.ensure_future(self._send_event("llm_perspective", {
            "messages": api_messages,
            "system_prompt": self.llm_system_prompt,
        }))

    # ── Initial data ───────────────────────────────────────────────────

    def on_initial_conversation(self, messages: list) -> None:
        """将已有会话发送给浏览器聊天面板。"""
        async def _send():
            for msg in messages[-100:]:  # Cap at 100 messages
                try:
                    output = agent_event_to_output(msg)
                    await self._send_event(output.type, output.data)
                except Exception:
                    pass
        asyncio.ensure_future(_send())

    def on_initial_system_prompt(self, system_prompt: str) -> None:
        """将系统提示发送给 LLM Perspective 面板。"""
        self.llm_system_prompt = system_prompt
        asyncio.ensure_future(self._send_event("llm_perspective", {
            "messages": [],
            "system_prompt": system_prompt,
        }))

    # ── Git Tree (AGT) ───────────────────────────────────────────────────

    def on_git_tree_update(self, tree_data: dict) -> None:
        """将 git 树布局发送给所有浏览器。"""
        self._last_tree_data = tree_data
        asyncio.ensure_future(self._send_event("git_tree_update", tree_data))

    # ── Handle incoming WebSocket messages ────────────────────────────────

    async def handle_ws_message(self, data: dict) -> None:
        """处理来自浏览器的消息。

        消息类型：
        - ``input_response`` / ``prompt``：用户输入（等待时）
        - ``inject``：在对话中途注入的 "btw" 消息（在下一次迭代时追加）
        - ``abort``：停止智能体（等效于 Ctrl+C）
        """
        kind = data.get("kind", "")

        if kind in ("input_response", "prompt"):
            value = data.get("value") or data.get("text", "")
            self.submit_input(str(value))

        elif kind == "inject":
            # "BTW" 消息 —— 注入到智能体的队列中，在下一轮循环迭代时
            # 被取出处理。
            value = data.get("text", "")
            if value:
                self._agent.inject_message(str(value))
                await self._send_event("system_message", {
                    "content": f"Message queued: {value[:80]}",
                    "level": "info",
                    "subtype": "informational",
                    "hide_in_ui": False,
                })

        elif kind == "abort":
            self._agent.abort()

        else:
            logger.debug("Unknown WS message kind: %s", kind)


# ── GUIConsole ────────────────────────────────────────────────────────────


class _GUIConsole(Console):
    """仅将输出发送给浏览器的 Rich Console 子类。

    不会写入终端。将 Rich 输出捕获为纯文本，
    并通过 WebSocket 发送。
    """

    def __init__(self, gui_ui: GUIUI) -> None:
        self._buf = io.StringIO()
        super().__init__(file=self._buf, width=120, no_color=True)
        self._gui_ui = gui_ui

    def print(self, *objects: Any, **kwargs: Any) -> None:
        self._buf.truncate(0)
        self._buf.seek(0)
        super().print(*objects, **kwargs)
        text = self._buf.getvalue().rstrip()
        self._buf.truncate(0)
        self._buf.seek(0)

        if text:
            try:
                asyncio.ensure_future(
                    self._gui_ui._send_event("local_output", {"text": text})
                )
            except RuntimeError:
                pass
