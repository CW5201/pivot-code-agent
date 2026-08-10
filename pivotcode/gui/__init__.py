"""Pivot Code GUI —— 带有 CLI 与 GUI 实现的 SessionUI 接口。

用法::

    # CLI 模式（默认）
    from pivotcode.gui.cli_ui import CLIUI
    ui = CLIUI()

    # GUI 模式（--gui）
    from pivotcode.gui.gui_ui import GUIUI
    ui = GUIUI(agent, cwd)
    await ui.start()

    # 测试
    from pivotcode.gui.scripted_ui import ScriptedUI
    ui = ScriptedUI.from_inputs(["Fix the bug", EOFError])
"""

from pivotcode.gui.base import SessionUI

__all__ = ["SessionUI"]
