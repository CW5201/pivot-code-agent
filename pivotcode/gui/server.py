"""Pivot Code GUI 的 FastAPI 服务器。

提供浏览器 SPA，并提供一个 WebSocket 端点。
作为 asyncio 后台任务与会话循环并行运行。
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from pivotcode.gui.gui_ui import GUIUI

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_PORT = 8420
MAX_PORT_ATTEMPTS = 10


def _find_available_port(start: int = DEFAULT_PORT, attempts: int = MAX_PORT_ATTEMPTS) -> int:
    """从 *start* 开始查找一个可用端口。

    使用 ``SO_REUSEADDR``，使得处于 TCP ``TIME_WAIT`` 状态的端口
    （来自刚刚被终止的服务器）也被视为可用。
    """
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(
        f"No available port found in range {start}-{start + attempts - 1}"
    )


def _cwd_url_segment(cwd: str) -> str:
    """路径的最后一部分，用作 URL 段。"""
    return Path(cwd).name or "pivot"


def create_gui_app(
    gui_ui: GUIUI, cwd: str = "", *, label: str | None = None
) -> FastAPI:
    """为 GUI 创建 FastAPI 应用。

    Args:
        gui_ui: 支撑该服务器的 GUIUI 实例。
        cwd: 工作目录；会在 /api/session 中回显。
        label: 可选的 URL 路径段覆盖值。为 None 时回退到
            ``Path(cwd).name``。
    """
    app = FastAPI(title="Pivot Code GUI", docs_url=None, redoc_url=None)
    project_name = label or _cwd_url_segment(cwd)

    # ── 会话信息端点 ─────────────────────────────────────────

    @app.get("/api/session")
    async def session_info():
        agent = gui_ui._agent
        return {
            "session_id": agent.session_id if agent else "",
            "session_name": agent._session.session_name if agent else "",
            "project": project_name,
            "cwd": cwd,
            "model": agent._model if agent else "",
        }

    # ── WebSocket 端点 ────────────────────────────────────────────

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        gui_ui.add_connection(websocket)

        # 发送历史以重放
        await gui_ui.send_history(websocket)

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "Ignored non-JSON WebSocket frame (%s): %.200r",
                        exc, raw,
                    )
                    continue
                await gui_ui.handle_ws_message(data)

        except WebSocketDisconnect:
            gui_ui.remove_connection(websocket)
        except Exception:
            gui_ui.remove_connection(websocket)

    # ── 静态文件 ──────────────────────────────────────────────────

    @app.get(f"/{project_name}/")
    @app.get(f"/{project_name}")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/")
    async def root_redirect():
        return RedirectResponse(url=f"/{project_name}/")

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app


async def start_gui_server(
    gui_ui: GUIUI,
    cwd: str = "",
    port: int | None = None,
    *,
    label: str | None = None,
) -> tuple[str, uvicorn.Server, asyncio.Task]:
    """以后台 asyncio 任务方式启动 GUI 服务器。

    返回 ``(url, server, task)``，以便调用方能够干净地将其关闭。

    Args:
        gui_ui: 支撑该服务器的 GUIUI 实例。
        cwd: 工作目录。
        port: 要绑定的端口；为 None 时自动选择。
        label: 可选的 URL 路径段覆盖值。为 None 时回退到
            ``cwd`` 的基名。
    """
    import uvicorn

    if port is None:
        port = _find_available_port()

    app = create_gui_app(gui_ui, cwd=cwd, label=label)
    project_name = label or _cwd_url_segment(cwd)
    url = f"http://localhost:{port}/{project_name}/"

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        # 关闭时不要永远等待 websocket 关闭。
        timeout_graceful_shutdown=1,
    )
    server = uvicorn.Server(config)

    logger.info("GUI server starting at %s", url)
    task = asyncio.create_task(server.serve())

    # 等待服务器完成绑定
    for _ in range(50):
        await asyncio.sleep(0.1)
        if server.started:
            break
    else:
        logger.warning("GUI server did not start within 5 seconds")

    return url, server, task
