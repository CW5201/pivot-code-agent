"""Pivot Code 命令行入口。

用法：
    pivotcode                                    # 启动交互式会话
    pivotcode --resume                           # 恢复最近的会话
    pivotcode --model openrouter/google/gemini-2.5-flash
    pivotcode --print "fix the bug in main.py"   # 非交互式运行
    pivotcode --version
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from pivotcode.__version__ import __version__
from pivotcode.agent import PivotCodeAgent
from pivotcode.cli.repl import run_session
from pivotcode.messages.types import AssistantMessage, TextBlock
from pivotcode.session.session import find_session_by_prefix, get_last_session_id
from pivotcode.settings import get_settings_path

# ── .env 文件加载 ──────────────────────────────────────────────────────────────
# 自动加载最近的 .env 文件（项目根目录或 .pivot/），使
# DASHSCOPE_API_KEY 等 API 密钥无需手动导出即可使用。
try:
    from dotenv import load_dotenv
    # 搜索顺序：项目根目录、项目的 .pivot/，然后是全局
    # ~/.pivot/.env，这样无论在哪启动 agent 都能工作，而无需
    # 为每个项目重复定义 API 密钥。
    _home = Path.home()
    for _candidate in (
        Path(".env"),
        Path(".pivot") / ".env",
        _home / ".pivot" / ".env",
    ):
        if _candidate.is_file():
            load_dotenv(_candidate, override=False)
            break
except ImportError:
    pass


def main() -> None:
    """``pivotcode`` 的入口函数。

    解析命令行参数，确定要运行的模式（交互式 CLI、浏览器 GUI
    或非交互式打印），在需要时执行首次运行设置，并分发到对应的
    运行器。

    这正是 ``pip install pivotcode`` 通过 ``pyproject.toml`` 中
    的 ``[project.scripts]`` 条目绑定到 ``pivotcode`` 可执行文件的函数。
    """
    parser = argparse.ArgumentParser(
        description="Pivot Code -- Open-source Coding Agent (CLI mode)"
    )

    # 设置类参数 —— 全部默认 None，以便检测“是否传入”
    parser.add_argument(
        "--backend", default=None,
        help=("Transport backend (advanced). One of: auto, anthropic-native, "
              "scripted. Inferred from --model when not set."),
    )
    parser.add_argument(
        "--provider", default=None,
        help="Deprecated alias for --backend. Will be removed in a future release.",
    )
    parser.add_argument(
        "--model", default=None,
        help=("Model to use. Bare names (gpt-4o, claude-sonnet-4-6) or "
              "LiteLLM-style provider/model prefixes "
              "(ollama/llama3, openrouter/google/gemini-2.5-pro, ...)."),
    )
    parser.add_argument("--api-key", default=None, help="API key")
    parser.add_argument("--base-url", default=None, help="API base URL (for local servers: http://localhost:8000/v1)")
    parser.add_argument("--tool-call-format", default=None, choices=["hermes", "glm", "pivot"],
                        help="Text-based tool call format for models without native tool calling")
    parser.add_argument("--permission-mode", default=None, choices=["yolo", "edit", "safe"])
    parser.add_argument(
        "--max-iterations-per-turn",
        type=int,
        default=None,
        help="Max API calls (iterations) per user message before the agent stops",
    )
    parser.add_argument("--max-output-tokens", type=int, default=None)
    parser.add_argument("--memory", default=None, choices=["on", "off", "intensive"])
    parser.add_argument("--verbose", default=None, action="store_true")

    # 非设置类参数
    parser.add_argument("--print", dest="print", default=None, metavar="PROMPT",
                        help="Non-interactive: run prompt and exit")
    parser.add_argument("--resume", action="store_true", help="Resume last session")
    parser.add_argument("--continue", dest="continue_session", default=None, nargs="?", const="__LIST__",
                        metavar="SESSION_PREFIX",
                        help="Continue a session. Without arg: list recent sessions. With arg: resume by prefix match.")
    parser.add_argument("--version", action="store_true", help="Show version")
    parser.add_argument("--gui", action="store_true", default=False,
                        help="Launch browser GUI alongside CLI (http://localhost:8420/)")

    # 解析并提取非设置类参数
    args = parser.parse_args()
    all_args = vars(args)

    print_instructions = all_args.pop("print")
    do_resume = all_args.pop("resume")
    continue_prefix = all_args.pop("continue_session")
    do_show_version = all_args.pop("version", None)
    do_gui = all_args.pop("gui", False)

    # 显示版本
    if do_show_version:
        print(f"pivotcode {__version__}")
        sys.exit(0)

    # 解析会话
    cwd = os.getcwd()
    session_id = None
    if do_resume:
        session_id = get_last_session_id(cwd=cwd)
        if not session_id:
            print("错误：未找到之前的会话。", file=sys.stderr)
            sys.exit(1)
    elif continue_prefix == "__LIST__":
        _list_recent_sessions(cwd)
        sys.exit(0)
    elif continue_prefix:
        session_id = find_session_by_prefix(cwd, continue_prefix)
        if not session_id:
            print(f"错误：没有匹配 '{continue_prefix}' 的唯一会话。", file=sys.stderr)
            sys.exit(1)

    # 协调 --backend / --provider（已弃用的别名）。在构建
    # settings_cli 之前完成，以免把旧的键继续传递下去。
    legacy_provider = all_args.pop("provider", None)
    if legacy_provider is not None:
        if all_args.get("backend") is not None:
            print(
                "错误：请使用 --backend 或 --provider，但不要同时使用两者。",
                file=sys.stderr,
            )
            sys.exit(2)
        from pivotcode.settings import _LEGACY_PROVIDER_MAP

        mapped = _LEGACY_PROVIDER_MAP.get(str(legacy_provider).lower())
        if mapped is None:
            # 用户可能输入了类似 --provider ollama，以为
            # ollama 是一个后端。提示正确的用法。
            print(
                f"错误：'{legacy_provider}' 不是有效的后端。\n"
                f"       Valid backends: auto, anthropic-native, scripted.\n"
                f"       To use {legacy_provider}, pass it as part of the model "
                f"name: --model {legacy_provider}/<model-name>",
                file=sys.stderr,
            )
            sys.exit(2)
        all_args["backend"] = mapped

    # CLI 设置 = 非 None 的参数，转换为正确的类型
    from pivotcode.settings import coerce_value
    settings_cli = {}
    for k, v in all_args.items():
        if v is not None:
            settings_cli[k] = coerce_value(v) if isinstance(v, str) else v

    # 日志
    if settings_cli.get("verbose"):
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    # 首次运行设置：检测 API 密钥并配置默认项
    if not get_settings_path(cwd).exists():
        _first_run_setup(cwd)

    if print_instructions is not None:
        # 非交互模式 —— 无 UI，直接运行并打印
        from pivotcode.cli.user_input import ask_user_cli
        ask_cb = ask_user_cli if sys.stdin.isatty() else None
        agent = PivotCodeAgent(
            session_id=session_id,
            ask_callback=ask_cb,
            **settings_cli,
        )
        asyncio.run(_run_print_mode(agent, prompt=print_instructions))
    elif do_gui:
        # GUI 模式 —— 基于浏览器的 UI
        asyncio.run(_run_gui_mode(session_id, settings_cli, cwd))
    else:
        # CLI 模式（默认）—— 终端 UI
        asyncio.run(_run_cli_mode(session_id, settings_cli))


async def _run_cli_mode(session_id, settings_cli):
    """标准终端模式。"""
    from pivotcode.gui.cli_ui import CLIUI
    ui = CLIUI()
    agent = PivotCodeAgent(
        session_id=session_id,
        ask_callback=ui.ask_user,
        **settings_cli,
    )
    await run_session(agent, ui, resumed_session_id=session_id)


async def _run_gui_mode(session_id, settings_cli, cwd):
    """浏览器 GUI 模式。"""
    from pivotcode.gui.gui_ui import GUIUI
    agent = PivotCodeAgent(
        session_id=session_id,
        ask_callback=None,  # Will be set to ui.ask_user below
        **settings_cli,
    )
    ui = GUIUI(agent, cwd)
    agent._ask_callback = ui.ask_user
    agent._llm_perspective_callback = ui.set_llm_perspective

    url = await ui.start()
    print(f"\n  GUI: {url}\n")
    print("  Open the URL in your browser. All interaction happens there.\n")

    try:
        await run_session(agent, ui, resumed_session_id=session_id)
    finally:
        await ui.stop()


# ── 会话列表 ──────────────────────────────────────────────────────────


def _list_recent_sessions(cwd: str, max_sessions: int = 10) -> None:
    """列出最近的会话，包含时间戳与最后一条用户消息。"""
    import json

    sessions_dir = Path(cwd) / ".pivot" / "sessions"
    if not sessions_dir.is_dir():
        print("No sessions found.")
        return

    # 收集包含转录信息的会话
    sessions = []
    for session_dir in sessions_dir.iterdir():
        if not session_dir.is_dir():
            continue
        transcript = session_dir / "transcript.jsonl"
        if not transcript.is_file():
            continue

        sid = session_dir.name
        created_at = None
        last_time = None
        last_user_msg = ""

        try:
            with open(transcript, "r") as f:
                lines = f.readlines()

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # 元数据行
                if "_metadata" in d:
                    meta = d["_metadata"]
                    created_at = meta.get("created_at", "")
                    continue

                # 记录最后一条用户消息（仅文本内容）
                msg_type = d.get("type") or d.get("role")
                if msg_type == "user":
                    content = d.get("content", "")
                    if isinstance(content, str) and content.strip() and not content.startswith("<system-reminder>"):
                        last_user_msg = content.strip()

            # 以转录文件的最近修改时间作为结束时间
            last_time = transcript.stat().st_mtime

        except (OSError, json.JSONDecodeError):
            continue

        sessions.append({
            "id": sid,
            "created_at": created_at or "",
            "last_time": last_time or 0,
            "last_user_msg": last_user_msg,
        })

    if not sessions:
        print("No sessions found.")
        return

    # 按最近修改时间排序，最新的在前
    sessions.sort(key=lambda s: s["last_time"], reverse=True)
    sessions = sessions[:max_sessions]

    print()
    print(f"  Recent sessions ({len(sessions)}):")
    print()

    from datetime import datetime as dt

    for s in sessions:
        sid_short = s["id"][:12]

        # 从 ISO 格式的 created_at 解析开始时间
        try:
            created = dt.fromisoformat(s["created_at"]).strftime("%Y-%m-%d %H:%M") if s["created_at"] else "?"
        except (ValueError, TypeError):
            created = "?"

        # 从文件 mtime 解析结束时间
        try:
            ended = dt.fromtimestamp(s["last_time"]).strftime("%H:%M") if s["last_time"] else "?"
        except (OSError, ValueError):
            ended = "?"

        time_display = f"{created} - {ended}" if created != "?" else "?"

        # 截断最后一条消息
        msg = s["last_user_msg"]
        if len(msg) > 60:
            msg = msg[:57] + "..."
        msg_display = f'  "{msg}"' if msg else ""

        print(f"    {sid_short}  | {time_display} |{msg_display}")

    print()
    print("  Use: pivotcode --continue <session_id_prefix>")
    print()


# ── 首次运行设置 ──────────────────────────────────────────────────────────


def _first_run_setup(cwd: str) -> None:
    """检测 API 密钥并显示首次运行引导。

    每个项目调用一次（当 .pivot/settings.json 尚不存在时）。
    根据环境中检测到的 API 密钥，打印欢迎信息并给出配置建议。
    """
    print()
    print("=" * 60)
    print("  欢迎使用 Pivot Code！")
    print("=" * 60)
    print()
    print("  当前项目的默认后端和模型：")
    print("    auto / openai/agnes-2.5-flash")
    print()

    # 检测 API 密钥
    detections = _detect_api_keys()
    if detections:
        print("  根据你的 API 密钥建议的配置：")
        print()
        for d in detections:
            print(f"    {d['label']}")
            print(f"      /settings-project model={d['model']}")
            if d.get("note"):
                print(f"      ({d['note']})")
            print()

    print("  要更改默认设置，请使用 /settings-project。")
    print("  此项目中所有未来的 Pivot Code 会话将默认使用这些设置。")
    print("  要更改当前会话的设置，请使用 /settings。")
    print("  要覆盖会话和项目设置，请在启动或恢复 Pivot Code 会话时")
    print("  在命令行使用 'pivotcode --<key> <value>'。")
    print()
    print("=" * 60)
    print()


def _detect_api_keys() -> list[dict]:
    """检测可用的 API 密钥并建议模型配置。

    后端从模型名称推断，因此我们只需要在这里显示正确的 ``--model`` 值。
    """
    detections = []

    if os.environ.get("DASHSCOPE_API_KEY"):
        detections.append({
            "label": "检测到 DASHSCOPE_API_KEY（阿里云 DashScope）",
            "model": "dashscope/qwen3.7-flash-2026-07-15",
            "note": "千问大模型；可用模型：qwen-plus, qwen-max, qwen-turbo, qwen-long, ...",
        })

    return detections


# ── 打印模式 ───────────────────────────────────────────────────────────────


async def _run_print_mode(agent: PivotCodeAgent, prompt: str) -> None:
    """以非交互方式运行一轮，并将回答打印到标准输出。

    由 ``pivotcode --print "some prompt"`` 使用 —— 在助手文本到达时
    流式输出到标准输出，捕获 Ctrl+C 以干净的 130 退出码结束，
    其他异常则通过 :func:`_display_error_stderr` 以退出码 1 暴露。

    Args:
        agent: 预配置好的 agent（通常带有 ``yolo`` 或提供的
            ``ask_callback`` —— 管道模式下交互式提示会很不方便）。
        prompt: 要运行的单条用户消息。
    """
    try:
        async for event in agent.query_events_async(prompt):
            # 只打印虚拟（流式）消息，避免重复输出
            if isinstance(event, AssistantMessage) and event.hide_in_api:
                for block in event.content:
                    if isinstance(block, TextBlock):
                        print(block.text, end="", flush=True)
    except KeyboardInterrupt:
        agent.abort()
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        _display_error_stderr(e)
        sys.exit(1)
    finally:
        await agent.close()
    print()


def _display_error_stderr(error: Exception) -> None:
    from pivotcode.cli.errors import classify_error
    message, _ = classify_error(error)
    print(f"\n{message}", file=sys.stderr)


if __name__ == "__main__":
    main()
