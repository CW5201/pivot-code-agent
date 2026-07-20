"""CLI 的富文本显示辅助函数。

负责使用 Rich 库渲染流式事件、欢迎横幅、成本汇总
以及工具结果。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from pivotcode.memory.memdir import PIVOT_MD
from pivotcode.messages.types import (
    AssistantMessage,
    AttachmentMessage,
    Message,
    ProgressMessage,
    RequestStartEvent,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

logger = logging.getLogger(__name__)

# ── 流式文本过滤状态 ─────────────────────────────────────────────────────────
# 跟踪流式输出过程中是否处于 <think> 或 <tool_call> 标签内部。
# 在每次渲染助手消息时重置。

_stream_state = {
    "in_thinking": False,
    "in_tool_call": False,
    "buffer": "",
    # 一旦为当前思考区块打印了 "thinking" 标题，即为 True。
    # 区块结束时清除，以便下次进入时再次打印标题。这样思考内容可与其
    # 他暗淡/斜体内容在视觉上区分开，而无需逐字符重复打印标签。
    "thinking_active": False,
}


_THINKING_LABEL = "[magenta]▎ Thinking:[/magenta] "


def _begin_thinking_section(console: Console) -> None:
    """如果当前区块尚未激活，则打印思考标题。"""
    if not _stream_state["thinking_active"]:
        console.print(_THINKING_LABEL, end="")
        _stream_state["thinking_active"] = True


def _end_thinking_section(console: Console) -> None:
    """用换行符关闭一个已激活的思考区块。"""
    if _stream_state["thinking_active"]:
        console.print()
        _stream_state["thinking_active"] = False


def _reset_stream_state(assume_thinking: bool = False) -> None:
    """重置流式显示状态（在每轮开始时调用）。

    Args:
        assume_thinking: 如果为 True，则以思考模式启动（适用于像
            GLM 这类在 ``</think>`` 之前、没有开头 ``<think>`` 标签
            就直接输出推理文本的模型）。
    """
    _stream_state["in_thinking"] = assume_thinking
    _stream_state["in_tool_call"] = False
    _stream_state["buffer"] = ""
    _stream_state["thinking_active"] = False


def _stream_text_delta(text: str, console) -> None:
    """显示一个流式文本增量，并过滤掉 think/tool_call 标记。

    - ``<think>...</think>`` 内部的文本以暗淡斜体显示。
    - ``<tool_call>...</tool_call>`` 内部的文本被抑制（解析后作为
      结构化的工具调用块显示）。
    - 普通文本正常显示。
    """
    buf = _stream_state["buffer"] + text
    _stream_state["buffer"] = ""

    # 如果本轮进入时已经处于思考模式（assume_thinking=True，
    # 适用于在任意开头标签之前就输出推理文本的模型），确保
    # 在第一个字符前打印标题。
    if _stream_state["in_thinking"]:
        _begin_thinking_section(console)

    i = 0
    while i < len(buf):
        # 检查标签开头
        if buf[i] == "<":
            # 检查是否拥有完整标签，还是需要缓冲
            remaining = buf[i:]

            # <think>
            if remaining.startswith("<think>"):
                _stream_state["in_thinking"] = True
                _begin_thinking_section(console)
                i += len("<think>")
                continue
            # </think>
            if remaining.startswith("</think>"):
                _stream_state["in_thinking"] = False
                _end_thinking_section(console)
                i += len("</think>")
                continue
            # <tool_call>
            if remaining.startswith("<tool_call>"):
                _stream_state["in_tool_call"] = True
                i += len("<tool_call>")
                continue
            # </tool_call>
            if remaining.startswith("</tool_call>"):
                _stream_state["in_tool_call"] = False
                i += len("</tool_call>")
                continue

            # 末尾可能是不完整的标签 —— 缓冲起来
            if len(remaining) < 13:  # 标签最大长度：</tool_call>
                _stream_state["buffer"] = remaining
                return
            # 不是已知标签 —— 打印 '<' 并继续

        char = buf[i]
        if _stream_state["in_tool_call"]:
            pass  # 抑制工具调用标记
        elif _stream_state["in_thinking"]:
            console.print(f"[dim italic]{char}[/dim italic]", end="")
        else:
            console.print(char, end="", highlight=False)
        i += 1


def display_welcome(console: Console, agent: Any) -> None:
    """在会话开始时显示欢迎横幅。"""
    model = agent._model
    session_short = agent.session_id[:8]
    cwd = agent._cwd or ""
    has_pivot_md = Path(cwd, PIVOT_MD).is_file() if cwd else False

    hint = ""
    if not has_pivot_md:
        hint = f"\n[dim]提示：创建 {PIVOT_MD}（或使用 /init）来提供项目上下文[/dim]"

    console.print(
        Panel.fit(
            f"[bold blue]Pivot Code[/bold blue] -- 开源编程助手\n"
            f"会话：{session_short}... | 模型：{model}\n"
            f"输入 /help 查看命令，Ctrl+C 中断{hint}",
            border_style="blue",
        )
    )


def display_event(event: StreamEvent | Message, console: Console) -> None:
    """向控制台显示一个流事件或消息。

    路由逻辑：
    - hide_in_api=True 的 AssistantMessage：流式文本增量，内联打印。
    - hide_in_api=False 的 AssistantMessage：最终组装完成的消息，渲染为 Markdown。
    - UserMessage：若包含工具结果，则在面板中展示。
    - SystemMessage：暗淡的 informational 文本。
    - RequestStartEvent：思考指示器。
    - AttachmentMessage：展示附件信息。
    - ProgressMessage：展示进度信息。
    """
    if isinstance(event, RequestStartEvent):
        # 不要打印 "Thinking..." —— 对非思考类模型而言既嘈杂又容易误导。
        # 流式文本很快就会显示出来。
        return

    if isinstance(event, AssistantMessage):
        _display_assistant_message(event, console)
        return

    if isinstance(event, UserMessage):
        _display_user_message(event, console)
        return

    if isinstance(event, SystemMessage):
        _display_system_message(event, console)
        return

    if isinstance(event, AttachmentMessage):
        _display_attachment_message(event, console)
        return

    if isinstance(event, ProgressMessage):
        _display_progress_message(event, console)
        return


def _display_assistant_message(msg: AssistantMessage, console: Console) -> None:
    """渲染一条助手消息。

    显示顺序：思考（暗淡斜体）→ 文本 → 工具调用。
    流式增量（hide_in_api=True）内联显示文本/思考。
    最终消息只显示工具调用（文本已经流式输出过）。
    """
    if msg.hide_in_api:
        # 流式增量 —— 内联打印，不带结尾换行符。
        for block in msg.content:
            if isinstance(block, TextBlock):
                # 如果思考仍处于激活状态（例如原生 ThinkingBlock 在任何
                # 文本之前流式输出），关闭该区块，使文本从新行开始。
                _end_thinking_section(console)
                _stream_text_delta(block.text, console)
            elif isinstance(block, ThinkingBlock) and block.thinking.strip():
                _begin_thinking_section(console)
                console.print(f"[dim italic]{block.thinking}[/dim italic]", end="")
        return

    # 最终组装完成的消息 —— 文本和思考已经流式输出过，
    # 因此只显示工具调用以及任何未流式输出的思考。
    has_text = any(isinstance(b, TextBlock) and b.text.strip() for b in msg.content)
    has_thinking = any(
        isinstance(b, ThinkingBlock) and b.thinking.strip() for b in msg.content
    )

    # 如果存在已流式输出的内容，则关闭流式行
    if has_text or has_thinking:
        _end_thinking_section(console)
        console.print()

    # 先显示思考（如果尚未流式输出 —— 即流式之后才被提取出来）
    for block in msg.content:
        if isinstance(block, ThinkingBlock) and block.thinking.strip():
            # 仅当处于非流式上下文时显示（由文本工具解析器提取）
            if not has_text:
                # 思考本身就是回复 —— 完整显示
                console.print(
                    f"{_THINKING_LABEL}[dim italic]{block.thinking.strip()}[/dim italic]"
                )
            # 如果 has_text 为真，思考已流式输出，或将通过流式
            # 路径作为预览显示 —— 不要重复显示

    # 显示工具调用
    for block in msg.content:
        if isinstance(block, ToolUseBlock):
            display_tool_use(block.name, block.input, console)


def _display_user_message(msg: UserMessage, console: Console) -> None:
    """渲染一条用户消息 —— 通常是工具结果。"""
    if msg.hide_in_ui or msg.hide_in_api:
        return

    if isinstance(msg.content, str):
        # 纯文本用户消息 —— 通常在 REPL 中不显示，因为用户
        # 已经输入过，但需优雅处理。
        return

    for block in msg.content:
        if isinstance(block, ToolResultBlock):
            result_text = (
                block.content
                if isinstance(block.content, str)
                else "".join(b.text for b in block.content if isinstance(b, TextBlock))
            )
            display_tool_result(
                tool_name=block.tool_use_id,
                result_text=result_text,
                is_error=block.is_error,
                console=console,
            )


def _display_system_message(msg: SystemMessage, console: Console) -> None:
    """以暗淡样式渲染一条系统消息。"""
    style_map = {
        "info": "dim",
        "warning": "yellow",
        "error": "red",
    }
    style = style_map.get(msg.level, "dim")
    console.print(f"  [{style}]{msg.content}[/{style}]")


def _display_attachment_message(msg: AttachmentMessage, console: Console) -> None:
    """显示一条附件通知。"""
    att = msg.attachment
    label = att.type.replace("_", " ").title()
    preview = att.content[:120] + "..." if len(att.content) > 120 else att.content
    console.print(
        Panel(
            preview or "[dim]<no content>[/dim]",
            title=f"[cyan]Attachment: {label}[/cyan]",
            border_style="cyan",
            expand=False,
        )
    )


def _display_progress_message(msg: ProgressMessage, console: Console) -> None:
    """显示一条进度更新。"""
    data = msg.data
    label = data.get("label", "Progress")
    console.print(f"  [dim]{label}[/dim]")


def display_tool_use(tool_name: str, tool_input: dict, console: Console) -> None:
    """显示工具调用的标题。"""
    # 显示工具调用的简洁摘要。
    input_summary = ", ".join(
        f"{k}={_truncate(str(v), 60)}" for k, v in tool_input.items()
    )
    console.print(
        Panel.fit(
            f"[bold]{tool_name}[/bold]({input_summary})",
            border_style="green",
            title="[green]工具调用[/green]",
        )
    )


DIFF_SENTINEL = "[ALAN-DIFF]"


def display_tool_result(
    tool_name: str,
    result_text: str,
    is_error: bool,
    console: Console,
) -> None:
    """在带样式的面板中显示工具结果。

    以 ``[ALAN-DIFF]`` 为前缀的 Edit/Write 结果会被渲染为带行号、着色的
    统一 diff，而不是普通面板。
    """
    if not is_error and result_text.startswith(DIFF_SENTINEL):
        _display_diff_result(result_text, console)
        return

    border = "red" if is_error else "green"
    title_label = "错误" if is_error else "结果"
    title_style = "red" if is_error else "green"

    # 为显示截断过长的结果。
    display_text = _truncate(result_text, 2000)

    console.print(
        Panel(
            display_text or "[dim]<empty>[/dim]",
            title=f"[{title_style}]{title_label}: {tool_name}[/{title_style}]",
            border_style=border,
            expand=False,
        )
    )


def _display_diff_result(result_text: str, console: Console) -> None:
    """以带行号和着色的方式渲染统一 diff 工具结果。

    预期格式（由 FileEditTool / FileWriteTool 生成）：

        [ALAN-DIFF]
        --- /path/to/file
        +++ /path/to/file
        @@ -start,len +start,len @@
         context
        -removed
        +added
        ...
        <纯文本摘要行>
    """
    body = result_text[len(DIFF_SENTINEL):].lstrip("\n")
    lines = body.splitlines()

    # 定位 diff 主体的结束位置（直到末尾看起来不像 diff
    # 内容的摘要行之前的所有内容）。
    diff_end = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        ln = lines[i]
        if ln.startswith((" ", "+", "-", "@", "\\")):
            diff_end = i + 1
            break
    diff_lines = lines[:diff_end]
    summary = "\n".join(lines[diff_end:]).strip()

    # 解析头部以提取文件路径。
    file_path = ""
    for ln in diff_lines[:4]:
        if ln.startswith("+++ "):
            file_path = ln[4:].strip()
            break

    # 统计新增/删除行数（跳过 +++/--- 头部行）。
    added = sum(
        1 for ln in diff_lines
        if ln.startswith("+") and not ln.startswith("+++")
    )
    removed = sum(
        1 for ln in diff_lines
        if ln.startswith("-") and not ln.startswith("---")
    )

    rendered = _render_diff_lines(diff_lines)

    title = f"[bold cyan]● Update[/bold cyan]([cyan]{file_path}[/cyan])"
    subtitle_parts: list[str] = []
    if added:
        subtitle_parts.append(f"[green]+{added}[/green]")
    if removed:
        subtitle_parts.append(f"[red]-{removed}[/red]")
    subtitle = "  ".join(subtitle_parts) if subtitle_parts else "[dim]no change[/dim]"

    console.print(
        Panel(
            rendered,
            title=f"{title}   {subtitle}",
            border_style="cyan",
            expand=False,
        )
    )
    if summary:
        console.print(f"[dim]{summary}[/dim]")


def _render_diff_lines(lines: list[str]) -> Text:
    """将统一 diff 行转换为带行号和着色的 Rich Text。"""
    text = Text()
    new_num = 0
    old_num = 0
    # 行号列宽 —— 随着遇到 hunk 头而增长。
    width = 3

    for ln in lines:
        if ln.startswith("---") or ln.startswith("+++"):
            # 文件头 —— 跳过。
            continue
        if ln.startswith("@@"):
            # Hunk 头，例如 "@@ -172,3 +172,8 @@"
            old_num, new_num = _parse_hunk_header(ln)
            width = max(width, len(str(new_num + len(lines))))
            text.append(f"{ln}\n", style="dim cyan")
            continue
        if ln.startswith("\\"):
            # "\ No newline at end of file" —— 暗淡显示。
            text.append(f"{'':>{width}}  {ln}\n", style="dim")
            continue
        if ln.startswith("+"):
            text.append(f"{new_num:>{width}} + ", style="green")
            text.append(ln[1:] + "\n", style="green")
            new_num += 1
        elif ln.startswith("-"):
            text.append(f"{old_num:>{width}} - ", style="red")
            text.append(ln[1:] + "\n", style="red")
            old_num += 1
        else:
            # 上下文行（以空格开头）。
            content = ln.removeprefix(" ")
            text.append(f"{new_num:>{width}}   ", style="dim")
            text.append(content + "\n")
            old_num += 1
            new_num += 1
    return text


def _parse_hunk_header(header: str) -> tuple[int, int]:
    """从 ``@@ -a,b +c,d @@`` 中提取 (old_start, new_start)。"""
    import re
    m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", header)
    if not m:
        return (1, 1)
    return (int(m.group(1)), int(m.group(2)))


def display_cost(agent: Any, console: Console) -> None:
    """在一轮结束后显示 token 用量与成本汇总。"""
    usage = agent.usage
    token_str = f"  [dim]Tokens: {usage.total_input:,} in / {usage.output_tokens:,} out"
    if agent.cost_unknown:
        console.print(f"{token_str}[/dim]")
    else:
        cost = agent.cost_usd
        console.print(f"{token_str} | Estimated cost: ${cost:.4f}[/dim]")


def _truncate(text: str, max_len: int) -> str:
    """截断字符串，若超过 max_len 则追加 '...'。"""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def display_replay_message(msg: Message, console: Console) -> None:
    """为回放渲染单条消息（例如会话恢复时）。

    与 :func:`display_event` 不同，这里不假设文本已经实时流式
    输出过。助手文本渲染为 Markdown；用户提示与工具结果均会
    显示；隐藏于 UI 的项会被跳过。
    """
    # 助手消息：显示 思考 → 文本 → 工具调用（完整）。
    if isinstance(msg, AssistantMessage):
        if msg.hide_in_api:
            # 历史中存储的流式增量 —— 跳过，最终的消息
            # 同级项已携带相同内容。
            return
        for block in msg.content:
            if isinstance(block, ThinkingBlock) and block.thinking.strip():
                console.print(
                    f"{_THINKING_LABEL}[dim italic]{block.thinking.strip()}[/dim italic]"
                )
        for block in msg.content:
            if isinstance(block, TextBlock) and block.text.strip():
                console.print(Markdown(block.text))
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                display_tool_use(block.name, block.input, console)
        return

    # 用户消息：纯文本提示 或 工具结果。
    if isinstance(msg, UserMessage):
        if msg.hide_in_ui or msg.hide_in_api:
            return
        if isinstance(msg.content, str):
            content = msg.content
            if content.startswith("<system-reminder>"):
                return
            console.print(f"\n[dim]> {content}[/dim]")
            return
        for block in msg.content:
            if isinstance(block, ToolResultBlock):
                result_text = (
                    block.content
                    if isinstance(block.content, str)
                    else "".join(
                        b.text for b in block.content if isinstance(b, TextBlock)
                    )
                )
                display_tool_result(
                    tool_name=block.tool_use_id,
                    result_text=result_text,
                    is_error=block.is_error,
                    console=console,
                )
        return

    if isinstance(msg, SystemMessage):
        # 与实时显示样式相同。
        _display_system_message(msg, console)
        return

    # AttachmentMessage / ProgressMessage / 未知类型：回放时跳过。


def display_replay(
    messages: list[Message], console: Console, *, limit: int = 20
) -> None:
    """在会话恢复时，将消息列表末尾部分回放到控制台。"""
    if not messages:
        return
    total = len(messages)
    tail = messages[-limit:] if total > limit else messages
    omitted = total - len(tail)
    if omitted > 0:
        console.print(
            f"\n[dim]… {omitted} earlier message(s) omitted. "
            f"Showing last {len(tail)} of {total}.[/dim]"
        )
    for msg in tail:
        display_replay_message(msg, console)
