"""User interaction helpers for the CLI."""

import asyncio

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl


async def ask_user_cli(
    question: str,
    options: list[str],
    session=None,
) -> str:
    """用上下方向键选择、Enter 确认的交互式提问。

    高亮项为默认选项（首项）。Ctrl+C 视为取消（抛 ``CancelledError``，
    由 REPL 的中断处理统一成 "Turn interrupted"）。

    渲染在 prompt_toolkit 应用内完成，避免与外层 rich 输出打架。
    """
    if not options:
        return ""

    current = 0

    def _render():
        fragments = [("bold yellow", f"? {question}\n\n")]
        for i, opt in enumerate(options):
            if i == current:
                # 高亮：绿底白字加粗，明显区别于未选项
                fragments.append(("bg:ansigreen ansiwhite bold", f"  ❯ {opt}\n"))
            else:
                fragments.append(("ansiwhite", f"    {opt}\n"))
        fragments.append(
            ("dim", "\n  ↑/↓ 移动选择 · Enter 确认 · Ctrl+C 取消\n")
        )
        return fragments

    control = FormattedTextControl(_render, focusable=True)

    kb = KeyBindings()

    def _redraw():
        control.text = _render()

    @kb.add("up")
    def _(event):
        nonlocal current
        current = (current - 1) % len(options)
        _redraw()

    @kb.add("down")
    def _(event):
        nonlocal current
        current = (current + 1) % len(options)
        _redraw()

    @kb.add("enter")
    def _(event):
        event.app.exit(result=options[current])

    @kb.add("c-c")
    def _(event):
        event.app.exit(result=None)

    try:
        app = Application(
            layout=Layout(HSplit([Window(control)])),
            key_bindings=kb,
            mouse_support=False,
            full_screen=False,
        )
        result = await app.run_async()
    except (KeyboardInterrupt, EOFError):
        raise asyncio.CancelledError("User interrupted permission prompt")
    except Exception:
        # 没有真实控制台（非 tty / 管道里）时退回数字选择，避免直接崩溃。
        return await _fallback_numbered(question, options)

    if result is None:
        raise asyncio.CancelledError("User interrupted permission prompt")

    return result


async def _fallback_numbered(question: str, options: list[str]) -> str:
    """无交互控制台时的兜底：数字选择（与原行为一致）。"""
    from rich.console import Console

    console = Console()
    console.print(f"\n[bold yellow]? {question}[/bold yellow]")
    for i, opt in enumerate(options, 1):
        console.print(f"  [cyan]{i})[/cyan] {opt}")
    if options:
        console.print("  [dim]Or type your own answer[/dim]")

    try:
        choice = input("\nYour choice: ").strip()
    except (KeyboardInterrupt, EOFError):
        raise asyncio.CancelledError("User interrupted permission prompt")

    try:
        idx = int(choice)
        if 1 <= idx <= len(options):
            return options[idx - 1]
    except ValueError:
        pass

    return choice if choice else "No answer provided."
