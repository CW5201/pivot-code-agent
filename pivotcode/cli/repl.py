"""Pivot Code 的会话循环。

与 UI 无关：在 CLIUI（终端）或 GUIUI（浏览器）下行为一致。
所有输入/输出都经过 :class:`SessionUI` 接口。
斜杠命令使用 ``ui.console``（Rich Console 或 GUIConsole）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path

from rich.syntax import Syntax
from rich.table import Table

from pivotcode.agent import PivotCodeAgent
from pivotcode.cli.display import display_welcome
from pivotcode.cli.errors import classify_error
from pivotcode.compact.compact_auto import compaction_auto
from pivotcode.git_tree.layout import compute_layout
from pivotcode.git_tree.memory_snapshots import get_memory_diff
from pivotcode.git_tree.operations import (
    agt_all_revert,
    agt_conv_revert,
    agt_move,
    agt_revert,
    agt_revert_to,
)
from pivotcode.git_tree.parser import parse_git_tree
from pivotcode.gui.base import SessionUI
from pivotcode.memory.memdir import (
    PIVOT_MD,
    ensure_memory_structure,
    ensure_project_instructions,
    get_global_memory_dir,
    get_memory_dir,
    load_global_memory_index,
    load_global_project_instructions,
    load_memory_index,
    load_project_instructions,
)
from pivotcode.memory.prompt import build_memory_section, get_save_command_prompt
from pivotcode.messages.factory import create_user_message
from pivotcode.messages.types import Usage
from pivotcode.prompt.system_prompt import get_system_prompt
from pivotcode.settings import (
    coerce_value,
    get_settings_path,
    load_settings,
)
from pivotcode.utils.env import is_git_repo as _is_git_repo

logger = logging.getLogger(__name__)

SLASH_COMMANDS: dict[str, str] = {
    "/help": "Show available commands",
    "/clear": "Clear conversation and start fresh",
    "/compact": "Manually trigger conversation compaction",
    "/model": "Show or change the current model",
    "/backend": "Show or change the transport backend (auto, anthropic-native)",
    "/plan": "Toggle planning mode (agent plans before executing)",
    "/exit": "Exit Pivot Code",
    "/init": "Create PIVOT.md in the project root with a starter template",
    "/diff": "Show git diff of all uncommitted changes",
    "/status": "Show session info (model, tokens, cost, etc.)",
    "/settings": "Show or update session settings (key=value)",
    "/settings-project": "Show or update project settings in .pivot/settings.json",
    "/save": "Ask the agent to save noteworthy info from this conversation to memory",
    "/memory": "Show or change memory mode (on, off, intensive)",
    "/commit": "Stage and commit changes with an AI-generated commit message",
    "/name": "Set a name for this session (displayed in listings and GUI)",
    "/revert": "Revert N commits back (default 1). Discards uncommitted changes.",
    "/move": "Move agent to a commit SHA or branch name",
    "/convrevert": "Revert N steps in conversation (agent forgets, repo unchanged)",
    "/allrevert": "Revert both position and conversation by N steps",
    "/memodiff": "Show memory diff with last commit",
    "/skill": "Invoke a skill: /skill <name> [args] | /skill list | /skill create",
}


async def run_session(
    agent: PivotCodeAgent,
    ui: SessionUI,
    resumed_session_id: str | None = None,
) -> None:
    """运行交互式会话循环。

    适用于任意 :class:`SessionUI` 实现（CLI 或 GUI）。
    """
    console = ui.console

    display_welcome(console, agent)

    # 显示一行恢复会话的提示（同时适用于 CLI 和 GUI）。
    # UI 本身会通过 on_initial_conversation 回放对话尾部。
    if resumed_session_id and agent._messages:
        session_name = agent._session.session_name
        label = session_name or resumed_session_id[:12] + "..."
        console.print(
            f"[dim]Session {label} resumed " f"({len(agent._messages)} messages)[/dim]"
        )

    # 向 GUI 面板发送初始数据（避免第一轮之前面板为空）
    _send_git_tree_update(agent, ui)
    if agent._messages:
        ui.on_initial_conversation(agent._messages)
    try:
        mem_dir = get_memory_dir(agent._cwd)
        global_mem_dir = get_global_memory_dir()
        memory_section = build_memory_section(
            agent._memory_mode,
            str(mem_dir),
            load_memory_index(cwd=agent._cwd),
            global_memory_dir=str(global_mem_dir),
            global_memory_index=load_global_memory_index(),
        )
        global_instr = load_global_project_instructions()
        project_instr = load_project_instructions(agent._cwd)
        append_parts = [p for p in (global_instr, project_instr) if p]
        append_prompt = "\n\n".join(append_parts) if append_parts else None
        sp, _boundary = get_system_prompt(
            tools=agent._tools,
            skills=agent._skill_registry.list_all(),
            model=agent._model,
            cwd=agent._cwd,
            append_prompt=append_prompt,
            memory_section=memory_section,
            scratchpad_dir=str(agent._scratchpad_dir),
        )
        ui.on_initial_system_prompt("\n\n".join(sp))
    except Exception:
        pass

    while True:
        try:
            user_input = await ui.get_input("\n> ")

            if not user_input:
                continue

            # Slash commands
            if user_input.startswith("/"):
                should_exit = await _handle_slash_command(
                    user_input,
                    agent,
                    console,
                    ui,
                )
                if should_exit:
                    break
                continue

            # Regular prompt
            await _handle_prompt(agent, user_input, ui)

        except EOFError:
            console.print("\nGoodbye!", style="dim")
            break
        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print()
            continue

    await agent.close()


async def _handle_prompt(
    agent: PivotCodeAgent,
    prompt: str,
    ui: SessionUI,
) -> None:
    """向 agent 发送提示，并通过 UI 显示事件。"""
    tool_call_format = agent._settings.get("tool_call_format")
    ui.reset_stream_state(assume_thinking=tool_call_format is not None)
    ui.on_agent_start()

    interrupted = False
    try:
        async for event in agent.query_events_async(prompt):
            await ui.on_agent_event(event)

    except (KeyboardInterrupt, asyncio.CancelledError):
        # CLI 中的 Ctrl+C
        interrupted = True
        agent.abort()
    except Exception as e:
        logger.exception("Error during prompt handling")
        _display_error(e, ui.console)
    finally:
        # 检查是否触发了中止（GUI 中的停止按钮会设置该事件而不
        # 抛出异常 —— 循环只是结束）。
        if agent._abort_event.is_set():
            interrupted = True
            agent._abort_event.clear()

        if interrupted:
            ui.console.print("[yellow]已中断当前操作。[/yellow]")

        try:
            # 对话规模 = 上次调用的权威用量（输入 + 输出）。在全新会话中、
            # 任何调用完成之前，或当 provider 未填充 `usage` 时为零。
            lu = agent.last_usage
            conv_tokens = lu.input_tokens + lu.output_tokens
            try:
                model_info = agent._provider.get_model_info(agent._model)
                ctx_window = model_info.context_window
            except Exception:
                ctx_window = 0
            await ui.on_cost(
                agent.usage,
                agent.cost_usd,
                agent.cost_unknown,
                conversation_tokens=conv_tokens,
                context_window=ctx_window,
            )
        except Exception:
            pass
        ui.on_agent_done()
        _send_git_tree_update(agent, ui)


def _send_git_tree_update(agent: PivotCodeAgent, ui: SessionUI) -> None:
    """向 UI 发送 git 树布局（非关键，错误会被忽略）。"""
    try:
        if not _is_git_repo(agent.cwd):
            return

        # 将 agent_position 与实际 HEAD 同步（防御性 —— 捕获遗漏的更新）
        _sync_agent_position(agent)

        tree = parse_git_tree(
            agent.cwd,
            pivot_commits=set(agent._session.pivot_commits),
        )
        layout = compute_layout(
            tree,
            conv_path=agent._session.conv_path,
            compaction_markers=agent._session.compaction_markers,
            agent_position=agent._session.agent_position_sha,
            session_root=agent._session.session_root_sha,
        )
        ui.on_git_tree_update(layout.to_json())
    except Exception:
        pass


def _sync_agent_position(agent: PivotCodeAgent) -> None:
    """若发生了外部变更，则将 agent_position_sha 与 HEAD 同步。

    仅当 agent_position_sha 与 HEAD 不同（意味着有外部操作移动了它）
    时才将 HEAD 加入 conv_path。若二者相同但 HEAD 不在 conv_path 中，
    则是有意为之（例如 /convrevert 之后），我们不干预。
    """
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=agent.cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return
        head = result.stdout.strip()
        state = agent._session

        if state.agent_position_sha and state.agent_position_sha != head:
            # HEAD changed externally — update position and conv_path
            state.agent_position_sha = head
            if head not in state.conv_path:
                state.add_to_conv_path(head)
        elif not state.agent_position_sha:
            # No position set yet — initialize
            state.agent_position_sha = head
            if head not in state.conv_path:
                state.add_to_conv_path(head)
    except Exception:
        pass


def _display_error(error: Exception, console) -> None:
    """对错误进行分类并以有帮助的信息展示。"""

    message, hint = classify_error(error)
    console.print(f"[red]{message}[/red]")
    if hint:
        console.print(f"[dim]{hint}[/dim]")


async def _handle_slash_command(
    command: str,
    agent: PivotCodeAgent,
    console,
    ui: SessionUI,
) -> bool:
    """处理斜杠命令。若会话应当退出则返回 True。"""
    parts = command.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/exit" or cmd == "/quit":
        console.print("再见！", style="dim")
        return True

    if cmd == "/help":
        _show_help(console)
        return False

    if cmd == "/clear":
        _handle_clear(agent, console)
        return False

    if cmd == "/compact":
        await _handle_compact(agent, console, arg)
        _send_git_tree_update(agent, ui)
        return False

    if cmd == "/model":
        _handle_model(agent, console, arg)
        return False

    if cmd == "/backend" or cmd == "/provider":
        _handle_backend(agent, console, arg, legacy_name=(cmd == "/provider"))
        return False

    if cmd == "/init":
        _handle_init(agent, console)
        return False

    if cmd == "/diff":
        _handle_diff(agent, console)
        return False

    if cmd == "/status":
        _handle_status(agent, console)
        return False

    if cmd == "/settings":
        _handle_settings(agent, console, arg)
        return False

    if cmd == "/settings-project":
        _handle_settings_project(agent, console, arg)
        return False

    if cmd == "/save":
        await _handle_save(agent, console, arg, ui)
        return False

    if cmd == "/memory":
        _handle_memory(agent, console, arg)
        return False

    if cmd == "/commit":
        await _handle_commit(agent, console, arg, ui)
        return False

    if cmd == "/name":
        _handle_name(agent, console, arg)
        return False

    if cmd == "/revert":
        await _handle_revert(agent, console, arg, ui)
        return False

    if cmd == "/move":
        await _handle_move(agent, console, arg, ui)
        return False

    if cmd == "/convrevert":
        await _handle_conv_revert(agent, console, arg, ui)
        return False

    if cmd == "/allrevert":
        await _handle_all_revert(agent, console, arg, ui)
        return False

    if cmd == "/memodiff":
        _handle_memodiff(agent, console, arg)
        return False

    if cmd == "/skill":
        await _handle_skill(agent, console, arg, ui)
        return False

    if cmd == "/plan":
        _handle_plan(agent, console)
        return False

    console.print(f"[yellow]未知命令：{cmd}[/yellow]  （输入 /help 查看命令列表）")
    return False


# ── Slash command implementations ──────────────────────────────────────────


def _show_help(console) -> None:
    """从 :data:`SLASH_COMMANDS` 打印完整的斜杠命令表。"""
    table = Table(title="可用命令", show_header=True, header_style="bold")
    table.add_column("Command", style="cyan", no_wrap=True)
    table.add_column("Description")
    for cmd, desc in SLASH_COMMANDS.items():
        table.add_row(cmd, desc)
    console.print(table)


def _handle_clear(agent: PivotCodeAgent, console) -> None:
    """丢弃内存中的对话并重置最近用量计数。

    磁盘上的会话文件会被保留（因此若用户想回到之前的状态，
    ``--resume`` 仍然可用），但内存中的消息列表会被清空，已持久化的
    ``last_usage`` 计数归零，使下一轮的“对话”数值从 0 开始。
    """
    agent._messages.clear()
    agent._last_usage = Usage()
    with agent._session.batch():
        agent._session.last_input_tokens = 0
        agent._session.last_output_tokens = 0
        agent._session.last_cache_read_tokens = 0
        agent._session.last_cache_write_tokens = 0
    console.print("[green]Conversation cleared.[/green]")


async def _handle_compact(agent: PivotCodeAgent, console, arg: str = "") -> None:
    """手动触发 Layer C（分叉 agent）式压缩。

    Args:
        arg: 追加到摘要生成器 prompt 的可选额外指令
            （例如 ``/compact focus on the bug we fixed``）。
    """
    msg_count = len(agent._messages)
    if msg_count <= 2:
        console.print(
            "[dim]Conversation is too short (less than 2 messages), nothing to compact.[/dim]"
        )
        return

    custom_instructions = arg.strip() if arg.strip() else None
    console.print(f"[dim]Compacting conversation ({msg_count} messages)...[/dim]")

    try:
        result = await compaction_auto(
            agent._messages,
            agent._provider,
            model=agent._model,
            custom_instructions=custom_instructions,
            session_id=agent.session_id,
            memory_mode=agent._memory_mode,
            settings=agent._settings,
        )
        if result:
            agent._messages = [result.boundary_message] + result.summary_messages
            console.print("[green]Conversation compacted successfully.[/green]")
            # AGT：记录压缩标记
            if _is_git_repo(agent.cwd) and agent._session.agent_position_sha:
                agent._session.add_compaction_marker(agent._session.agent_position_sha)
        else:
            console.print("[red]Compaction failed.[/red]")
    except Exception as e:
        logger.exception("Compaction error")
        console.print(f"[red]Compaction failed: {e}[/red]")


def _handle_model(agent: PivotCodeAgent, console, arg: str) -> None:
    """显示或更改当前模型。

    不带参数时打印当前模型；带参数时，会对照设置校验器进行校验，
    若更改被接受则重建 provider，并注入一个 ``<system-reminder>``
    以便 agent 知道后续消息可能来自不同的模型。
    """
    if arg:
        old_model = agent._model
        error = agent.update_session_setting("model", arg)
        if error:
            console.print(f"[red]{error}[/red]")
        else:
            console.print(f"[green]Model changed to: {arg}[/green]")
            new_backend = agent._settings.get("backend")
            console.print(
                f"[dim]Backend is now '{new_backend}' "
                "(use /backend <name> to override).[/dim]"
            )

            agent._messages.append(
                create_user_message(
                    f"<system-reminder>Model changed from {old_model} to {arg}. "
                    f"Previous messages may have been generated by a different model.</system-reminder>",
                    hide_in_ui=True,
                )
            )
    else:
        console.print(f"Current model: [bold]{agent._model}[/bold]")


def _handle_backend(
    agent: PivotCodeAgent, console, arg: str, *, legacy_name: bool = False,
) -> None:
    """显示或更改当前的传输后端。

    与 ``/model`` 不同，这里不会注入 ``<system-reminder>`` —— 后端
    只是传输路由，不影响模型所看到的内容。

    ``/provider`` 作为已弃用的别名被接受；使用时会打印一行提示。
    """
    if legacy_name:
        console.print(
            "[yellow]/provider is deprecated; use /backend.[/yellow]"
        )
    current = agent._settings.get("backend")
    if arg:
        error = agent.update_session_setting("backend", arg)
        if error:
            console.print(f"[red]{error}[/red]")
        else:
            console.print(f"[green]Backend changed to: {arg}[/green]")
    else:
        console.print(f"Current backend: [bold]{current}[/bold]")


def _handle_init(agent: PivotCodeAgent, console) -> None:
    """在项目根目录创建一个初始的 ``PIVOT.md``。

    若文件已存在则拒绝创建，以免覆盖已有文件。
    用户可自行编辑生成的模板。
    """
    cwd = agent.cwd
    path = Path(cwd) / PIVOT_MD
    if path.exists():
        console.print(f"[yellow]{PIVOT_MD} already exists at {path}[/yellow]")
        return
    result_path = ensure_project_instructions(cwd)
    console.print(f"[green]Created {PIVOT_MD} at {result_path}[/green]")


def _handle_diff(agent: PivotCodeAgent, console) -> None:
    """以语法高亮显示已暂存与未暂存的 git diff。"""
    cwd = agent.cwd
    if not _is_git_repo(cwd):
        console.print("[yellow]Not a git repository.[/yellow]")
        return

    try:
        unstaged = subprocess.run(
            ["git", "diff"], cwd=cwd, capture_output=True, text=True
        )
        staged = subprocess.run(
            ["git", "diff", "--staged"], cwd=cwd, capture_output=True, text=True
        )
    except FileNotFoundError:
        console.print("[red]git is not installed or not on PATH.[/red]")
        return

    combined = ""
    if staged.stdout.strip():
        combined += "# Staged changes\n" + staged.stdout
    if unstaged.stdout.strip():
        if combined:
            combined += "\n"
        combined += "# Unstaged changes\n" + unstaged.stdout

    if not combined.strip():
        console.print("[dim]No uncommitted changes.[/dim]")
        return

    syntax = Syntax(combined, "diff", theme="monokai", line_numbers=False)
    console.print(syntax)


def _handle_status(agent: PivotCodeAgent, console) -> None:
    """打印完整的会话状态表。

    包含后端、模型、会话 ID、轮次与消息数、token 明细
    （输入 / 缓存创建 / 缓存读取 / 输出）、预估成本、当前工作目录，
    以及 ``PIVOT.md`` / ``.pivot/settings.json`` 是否存在。
    """
    cwd = agent.cwd
    usage = agent.usage
    model = agent._model
    session_name = agent._session.session_name
    session_id_short = agent.session_id[:12]
    turn_count = agent.turn_count
    msg_count = len(agent._messages)

    pivot_md_exists = (Path(cwd) / PIVOT_MD).exists()
    settings_exists = get_settings_path(cwd).exists()

    backend = agent._settings.get("backend")

    table = Table(title="会话状态", show_header=False)
    table.add_column("Key", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row("Backend", str(backend))
    table.add_row("Model", str(model))
    table.add_row("会话 ID", session_id_short)
    if session_name:
        table.add_row("会话名称", session_name)
    table.add_row("Turns", str(turn_count))
    table.add_row("Messages", str(msg_count))
    table.add_row("Input tokens", f"{usage.input_tokens:,}")
    table.add_row("Cache creation tokens", f"{usage.cache_creation_input_tokens:,}")
    table.add_row("Cache read tokens", f"{usage.cache_read_input_tokens:,}")
    table.add_row("Total input", f"{usage.total_input:,}")
    table.add_row("Output tokens", f"{usage.output_tokens:,}")
    if agent.cost_unknown:
        table.add_row("Estimated cost", "unknown (model not in pricing registry)")
    else:
        no_cache_reported = (
            usage.cache_creation_input_tokens == 0
            and usage.cache_read_input_tokens == 0
        )
        suffix = " (estimate w/o cache)" if no_cache_reported else ""
        table.add_row("Estimated cost", f"${agent.cost_usd:.4f}{suffix}")
    table.add_row("Working directory", cwd)
    table.add_row(
        "PIVOT.md", "[green]yes[/green]" if pivot_md_exists else "[dim]no[/dim]"
    )
    table.add_row(
        ".pivot/settings.json",
        "[green]yes[/green]" if settings_exists else "[dim]no[/dim]",
    )
    console.print(table)


def _handle_settings(agent: PivotCodeAgent, console, arg: str) -> None:
    """显示或更新会话设置。

    不带参数时，以 JSON 形式打印当前生效的设置字典；使用
    ``key=value`` 时，会校验并应用更改。与后端相关的键
    （``backend``、``model``、``api_key``、``base_url``）会为
    会话的剩余部分触发一个新的 ``LLMProvider`` 实例。
    """
    if not arg:
        formatted = json.dumps(agent._settings, indent=2, default=str)
        syntax = Syntax(formatted, "json", theme="monokai", line_numbers=False)
        console.print(syntax)
        return

    if "=" not in arg:
        console.print(
            "[yellow]Usage: /settings key=value[/yellow]  (e.g. /settings model=openai/gpt-4o)"
        )
        return

    key, _, raw_value = arg.partition("=")
    key = key.strip()
    value = coerce_value(raw_value.strip())
    error = agent.update_session_setting(key, value)
    if error:
        console.print(f"[red]{error}[/red]")
    else:
        console.print(f"[green]Session setting updated: {key} = {value!r}[/green]")


def _handle_settings_project(agent: PivotCodeAgent, console, arg: str) -> None:
    """显示或更新 ``.pivot/settings.json`` 中的项目设置。

    与 ``/settings`` 不同，这里的更改不会影响当前会话 ——
    它们更新的是磁盘上的默认值，供未来的会话读取。
    """
    cwd = agent.cwd
    if not arg:
        settings = load_settings(cwd)
        if not settings:
            console.print("[dim]No .pivot/settings.json found. Using defaults.[/dim]")
            return
        formatted = json.dumps(settings, indent=2, default=str)
        syntax = Syntax(formatted, "json", theme="monokai", line_numbers=False)
        console.print(syntax)
        return

    if "=" not in arg:
        console.print("[yellow]Usage: /settings-project key=value[/yellow]")
        return

    key, _, raw_value = arg.partition("=")
    key = key.strip()
    value = coerce_value(raw_value.strip())
    error = agent.update_project_setting(key, value)
    if error:
        console.print(f"[red]{error}[/red]")
    else:
        console.print(
            f"[green]Project setting updated: {key} = {value!r} in .pivot/settings.json[/green]"
        )


async def _handle_save(
    agent: PivotCodeAgent, console, arg: str = "", ui: SessionUI | None = None
) -> None:
    """让 agent 回顾对话并将其持久化到记忆中。

    若记忆模式为 ``off``，则不做任何操作（仅警告）。否则注入
    ``/save`` 提示（见 ``pivotcode/memory/prompt.py``），并附上用户
    提供的额外上下文，然后通过 UI 运行一轮，以完成保存并渲染工具调用。
    """
    if agent._memory_mode == "off":
        console.print(
            "[yellow]Memory is disabled. Use '/memory [on/intensive]' to enable it first.[/yellow]"
        )
        return

    console.print(
        "[dim]Asking agent to review conversation and save to memory...[/dim]"
    )
    prompt = get_save_command_prompt()
    if arg.strip():
        prompt += (
            f"\n\nAdditional context from user memory update request: {arg.strip()}"
        )
    if ui:
        await _handle_prompt(agent, prompt, ui)


def _handle_memory(agent: PivotCodeAgent, console, arg: str) -> None:
    """显示或更改记忆模式（``off``、``on``、``intensive``）。

    不带参数时打印当前模式并提示可用选项；带参数时，会对照设置
    校验器进行校验、应用更改，并注入一个 ``<system-reminder>``
    以便 agent 知道模式已切换。
    """
    if not arg:
        console.print(f"Memory mode: [bold]{agent._memory_mode}[/bold]")
        console.print(
            "[dim]Options: on (use memory, save on request), off (disabled), "
            "intensive (use memory, proactive saves)[/dim]"
        )
        return

    mode = arg.strip().lower()
    old_mode = agent._memory_mode
    error = agent.update_session_setting("memory", mode)
    if error:
        console.print(f"[red]{error}[/red]")
    else:
        console.print(f"[green]Memory mode changed to: {mode}[/green]")

        agent._messages.append(
            create_user_message(
                f"<system-reminder>Memory mode changed from '{old_mode}' to '{mode}'.</system-reminder>",
                hide_in_ui=True,
            )
        )

    if mode != "off":
        ensure_memory_structure(agent.cwd)


async def _handle_commit(
    agent: PivotCodeAgent, console, arg: str = "", ui: SessionUI | None = None
) -> None:
    """让 agent 通过 ``GitCommit`` 工具起草并提交代码。

    若不在 git 仓库中或没有可提交的更改，则拒绝执行。否则运行一轮，
    由 prompt 指示 agent 检查 diff、按仓库风格起草简洁的提交信息并提交。

    Args:
        arg: 可选的用户指引（例如 ``/commit note that this
            fixes the PTL retry bug``），追加到 prompt 中。
    """
    cwd = agent._cwd

    if not _is_git_repo(cwd):
        console.print("[red]Not a git repository.[/red]")
        return

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if not result.stdout.strip():
            console.print("[dim]No changes to commit.[/dim]")
            return
    except Exception:
        console.print("[red]Failed to check git status.[/red]")
        return

    prompt = (
        "The user wants to commit their changes. Please:\n"
        "1. Inspect the changes with `git status`, `git diff`, and "
        "`git diff --staged`.\n"
        "2. Look at recent commit message style with `git log --oneline -5`.\n"
        "3. Draft a concise commit message (1-2 sentences) that follows the "
        "repo's style and captures the 'why' rather than the 'what'.\n"
        "4. Call the GitCommit tool with that message. GitCommit stages the "
        "changes, adds the Co-Authored-By trailer, and records the commit "
        "in the agent's tracked commits.\n"
        "5. Confirm the result with `git log --oneline -1`."
    )
    if arg.strip():
        prompt += f"\n\nUser's additional guidance for this commit: {arg.strip()}"

    console.print("[dim]Asking Pivot to commit...[/dim]")
    if ui:
        await _handle_prompt(agent, prompt, ui)


def _handle_name(agent: PivotCodeAgent, console, arg: str) -> None:
    """显示或设置会话的人类可读名称。

    该名称显示在会话列表（``pivotcode --continue``）和 GUI 标签页标题中。
    不参与任何功能性逻辑 —— 纯粹为了方便用户。
    """
    if not arg:
        name = agent._session.session_name
        if name:
            console.print(f"会话名称：[bold]{name}[/bold]")
        else:
            console.print(
                "[dim]No session name set. Use /name <text> to set one.[/dim]"
            )
        return

    agent._session.session_name = arg.strip()
    console.print(f"[green]Session named: {arg.strip()}[/green]")


# ── AGT movement commands ────────────────────────────────────────────────────


async def _handle_revert(
    agent: PivotCodeAgent,
    console,
    arg: str = "",
    ui: SessionUI | None = None,
) -> None:
    """回退仓库状态。接受 N（整数步数）或某个 SHA/分支目标。"""
    if not _is_git_repo(agent.cwd):
        console.print("[yellow]Requires a git repository.[/yellow]")
        return

    arg = arg.strip()
    if not arg:

        result = agt_revert(agent.cwd, agent._session, 1)
    elif arg.isdigit():
        n = int(arg)
        if n < 1:
            console.print("[yellow]N must be at least 1.[/yellow]")
            return

        result = agt_revert(agent.cwd, agent._session, n)
    else:
        # SHA or branch target — destructive revert to that point
        target_sha = _resolve_sha(agent.cwd, arg)
        if not target_sha:
            console.print(f"[red]Cannot resolve '{arg}'[/red]")
            return

        result = agt_revert_to(agent.cwd, agent._session, target_sha)

    if result.success:
        console.print(f"[green]{result.description}[/green]")
        agent._messages.append(
            create_user_message(
                f"<system-reminder>User reverted repo. {result.description}\n"
                "Re-read files before making assumptions about their current state.</system-reminder>",
                hide_in_ui=True,
            )
        )
        if ui:
            _send_git_tree_update(agent, ui)
    else:
        console.print(f"[red]{result.description}[/red]")


async def _handle_move(
    agent: PivotCodeAgent,
    console,
    arg: str = "",
    ui: SessionUI | None = None,
) -> None:
    """将 agent 移动到不同的提交或分支。

    安全（非破坏性）：检出目标、更新 agent 位置，并注入一个
    ``<system-reminder>`` 说明发生了什么，以便模型知道工作树已变更。

    Args:
        arg: 提交 SHA 或分支名。
    """
    if not _is_git_repo(agent.cwd):
        console.print("[yellow]Requires a git repository.[/yellow]")
        return

    target = arg.strip()
    if not target:
        console.print("[yellow]Usage: /move <commit-sha-or-branch>[/yellow]")
        return

    # 将分支名解析为 SHA
    import subprocess as _sp

    result = _sp.run(
        ["git", "rev-parse", target],
        cwd=agent.cwd,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        console.print(f"[red]Cannot resolve '{target}': {result.stderr.strip()}[/red]")
        return
    target_sha = result.stdout.strip()


    move = agt_move(agent.cwd, agent._session, target_sha)

    if move.success:
        console.print(f"[green]{move.description}[/green]")
        short_sha = target_sha[:10]
        ref_hint = (
            f" (ref '{target}')" if target != target_sha else ""
        )
        agent._messages.append(
            create_user_message(
                f"<system-reminder>User ran /move, checking out commit "
                f"{short_sha}{ref_hint}. The working tree now reflects that "
                f"commit — files on disk may have changed compared to what "
                f"you saw earlier. {move.description} Re-read files before "
                f"making assumptions about their current state.</system-reminder>",
                hide_in_ui=True,
            )
        )
        if ui:
            _send_git_tree_update(agent, ui)
    else:
        console.print(f"[red]{move.description}[/red]")


async def _handle_conv_revert(
    agent: PivotCodeAgent,
    console,
    arg: str = "",
    ui: SessionUI | None = None,
) -> None:
    """将对话回退到特定提交时的状态。

    接受 N（在 conv_path 中回退的步数）或某个 SHA/分支目标。
    将消息截断到该提交被创建时所处的精确位置。
    """
    if not _is_git_repo(agent.cwd):
        console.print("[yellow]Requires a git repository.[/yellow]")
        return

    arg = arg.strip()
    if not arg:
        n = 1
    elif arg.isdigit():
        n = int(arg)
    else:
        # SHA target — compute N as steps from end of conv_path to this SHA
        conv = agent._session.conv_path
        target_sha = _resolve_sha(agent.cwd, arg)
        if not target_sha:
            console.print(f"[red]Cannot resolve '{arg}'[/red]")
            return
        if target_sha not in conv:
            console.print(
                f"[yellow]{arg[:7]} is not in the conversation path.[/yellow]"
            )
            return
        idx = len(conv) - 1 - conv[::-1].index(target_sha)
        n = len(conv) - 1 - idx
        if n <= 0:
            console.print("[dim]Already at that point in conversation.[/dim]")
            return

    # Find the target SHA we're reverting to
    conv = agent._session.conv_path
    target_idx = max(0, len(conv) - 1 - n)
    target_sha = conv[target_idx] if target_idx < len(conv) else None


    result = agt_conv_revert(agent.cwd, agent._session, n)

    if result.success:
        console.print(f"[green]{result.description}[/green]")
        # Truncate messages precisely using commit_message_indices
        if result.steps_reverted > 0 and target_sha:
            _truncate_messages_to_commit(agent, target_sha)
        agent._messages.append(
            create_user_message(
                f"<system-reminder>User ran /convrevert. {result.description} "
                "The recent conversation history has been truncated and those "
                "earlier messages are gone from your context. The working tree "
                "is unchanged — this only affects the conversation.</system-reminder>",
                hide_in_ui=True,
            )
        )
        if ui:
            _send_git_tree_update(agent, ui)
    else:
        console.print(f"[red]{result.description}[/red]")


async def _handle_all_revert(
    agent: PivotCodeAgent,
    console,
    arg: str = "",
    ui: SessionUI | None = None,
) -> None:
    """同时回退仓库和对话。接受 N（步数）或 SHA 目标。"""
    if not _is_git_repo(agent.cwd):
        console.print("[yellow]Requires a git repository.[/yellow]")
        return

    arg = arg.strip()
    if not arg:
        n = 1
    elif arg.isdigit():
        n = int(arg)
    else:
        # SHA 目标 —— 仓库用 /move，对话用 convrevert
        target_sha = _resolve_sha(agent.cwd, arg)
        if not target_sha:
            console.print(f"[red]Cannot resolve '{arg}'[/red]")
            return
        # Move repo
        await _handle_move(agent, console, target_sha, ui)
        # Also revert conv to that point
        await _handle_conv_revert(agent, console, target_sha, ui)
        return


    result = agt_all_revert(agent.cwd, agent._session, n)

    if result.success:
        console.print(f"[green]{result.description}[/green]")
        # Truncate messages to the target commit
        target_sha = result.new_sha
        if target_sha:
            _truncate_messages_to_commit(agent, target_sha)
        agent._messages.append(
            create_user_message(
                f"<system-reminder>User ran /allrevert. {result.description} "
                "Both the working tree and the conversation were reverted: "
                "earlier messages are gone from your context, and the files "
                "on disk now reflect the earlier commit. Re-read files if "
                "you need them.</system-reminder>",
                hide_in_ui=True,
            )
        )
        if ui:
            _send_git_tree_update(agent, ui)
    else:
        console.print(f"[red]{result.description}[/red]")


def _truncate_messages_to_commit(agent: PivotCodeAgent, target_sha: str) -> None:
    """将消息截断到 *target_sha* 被提交时所处的精确位置。

    使用会话状态中的 ``commit_message_indices`` 以保证精确。
    若未记录索引则回退到启发式方法。
    """
    indices = agent._session.commit_message_indices
    if target_sha in indices:
        cutoff = indices[target_sha]
        if 0 < cutoff < len(agent._messages):
            agent._messages = agent._messages[:cutoff]
            return

    # 回退：未记录索引 —— 尝试在消息中找到 GitCommit 工具结果
    # 对 target_sha 的消息，并在其之后截断
    for i in range(len(agent._messages) - 1, -1, -1):
        msg = agent._messages[i]
        if hasattr(msg, "content") and isinstance(msg.content, list):
            for block in msg.content:
                if hasattr(block, "name") and block.name == "GitCommit":
                    # 检查该工具调用是否生成了目标提交
                    if hasattr(block, "input") and isinstance(block.input, dict):
                        # 无法可靠匹配 —— 继续查找
                        pass
        # 检查提及该 SHA 的工具结果
        if hasattr(msg, "content") and isinstance(msg.content, str):
            if target_sha[:7] in msg.content:
                agent._messages = agent._messages[: i + 1]
                return


def _resolve_sha(cwd: str, target: str) -> str | None:
    """将分支/标签/短 SHA 解析为完整的 SHA。"""
    import subprocess as _sp

    result = _sp.run(
        ["git", "rev-parse", target],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.strip() if result.returncode == 0 else None


async def _handle_skill(
    agent: PivotCodeAgent,
    console,
    arg: str,
    ui: SessionUI,
) -> None:
    """处理 /skill <name> [args] | /skill list | /skill create。"""
    # 重新扫描技能目录，以便无需重启会话即可发现新创建的技能。
    agent._skill_registry.reload(agent._cwd)

    parts = arg.strip().split(maxsplit=1)
    if not parts:
        console.print(
            "[yellow]Usage: /skill <name> [args] | /skill list | /skill create[/yellow]"
        )
        return

    subcmd = parts[0]
    skill_args = parts[1] if len(parts) > 1 else ""

    if subcmd == "list":
        _show_skills_list(agent, console)
        return

    # 查找技能（包含内置的 “create” 以及任何已发现的技能）
    skill = agent._skill_registry.get(subcmd)
    if skill is None:
        console.print(f"[yellow]未知技能：{subcmd}[/yellow]")
        console.print("[dim]Use /skill list to see available skills[/dim]")
        return

    expanded = agent._skill_registry.expand(subcmd, skill_args)
    # 若技能定义了 allowed-tools，则设置工具限制
    if skill.allowed_tools:
        agent._active_skill_filter = skill.allowed_tools
    console.print(f"[dim]Invoking skill: {skill.name}[/dim]")
    await _handle_prompt(agent, expanded, ui)


def _show_skills_list(agent: PivotCodeAgent, console) -> None:
    """在表格中显示所有已发现的技能。"""
    skills = agent._skill_registry.list_all()
    if not skills:
        console.print("[dim]No skills discovered.[/dim]")
        console.print(
            "[dim]Create one with /skill create or add SKILL.md files "
            "to .pivot/skills/<name>/[/dim]"
        )
        return

    table = Table(title="可用技能", show_header=True, header_style="bold")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Description")
    table.add_column("Source", style="dim")
    for skill in skills:
        source = "builtin" if skill.source_path == "<builtin>" else "disk"
        table.add_row(skill.name, skill.description, source)
    console.print(table)
    console.print("[dim]Invoke with: /skill <name> [args][/dim]")


def _handle_memodiff(agent: PivotCodeAgent, console, arg: str = "") -> None:
    if not _is_git_repo(agent.cwd):
        console.print("[yellow]Requires a git repository.[/yellow]")
        return


    current = agent._session.agent_position_sha
    if not current:
        console.print("[dim]No AGT position tracked yet.[/dim]")
        return

    # Find the previous pivot commit to diff against
    prev_commits = agent._session.pivot_commits
    if len(prev_commits) < 2:
        console.print("[dim]Not enough commits to show a memory diff.[/dim]")
        return

    prev = prev_commits[-2]
    diff = get_memory_diff(agent.cwd, prev, current)
    if diff:
        console.print(f"[bold]Memory diff ({prev[:7]} → {current[:7]}):[/bold]")
        console.print(diff)
    else:
        console.print("[dim]No memory differences found.[/dim]")


def _handle_plan(agent: PivotCodeAgent, console) -> None:
    """切换规划模式。

    启用后，agent 会在执行操作前先生成计划，
    并在继续之前请求用户批准。
    """
    # 切换规划模式标志
    if not hasattr(agent, "_planning_mode"):
        agent._planning_mode = False

    agent._planning_mode = not agent._planning_mode

    if agent._planning_mode:
        console.print("[green]Planning mode: ON[/green]")
        console.print("[dim]Agent will now create a plan before executing actions.[/dim]")
        console.print("[dim]Use /plan again to disable.[/dim]")

        # 将规划指令添加到系统提示中
        planning_prompt = (
            "\n\n## Planning Mode\n"
            "You are in PLANNING MODE. Before executing any action:\n"
            "1. First, present a clear plan with numbered steps\n"
            "2. Explain what each step will do\n"
            "3. Ask the user for approval with: 'Do you approve this plan? (yes/no)'\n"
            "4. ONLY proceed after the user approves\n"
            "5. If the user says no, revise the plan based on their feedback\n\n"
            "Format your plan as:\n"
            "## Plan\n"
            "1. [Step 1 description]\n"
            "2. [Step 2 description]\n"
            "...\n\n"
            "Do you approve this plan? (yes/no)"
        )

        # 保存原始的附加提示并加入规划提示
        if not hasattr(agent, "_original_append_prompt"):
            agent._original_append_prompt = agent._settings.get("append_system_prompt", "")
        agent._settings["append_system_prompt"] = (
            agent._original_append_prompt + planning_prompt
        )
    else:
        console.print("[yellow]Planning mode: OFF[/yellow]")
        console.print("[dim]Agent will now execute actions directly.[/dim]")

        # 恢复原始的附加提示
        if hasattr(agent, "_original_append_prompt"):
            agent._settings["append_system_prompt"] = agent._original_append_prompt
