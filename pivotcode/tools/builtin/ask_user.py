"""AskUserQuestion工具 - 让模型向用户提问，提供预设答案。

模型提供一个问题和一系列预设选项。
用户选择一个选项，或选择"其他"输入自定义响应。
选定的答案作为工具结果返回给模型。
"""

from typing import Any

from pivotcode.tools.base import Tool, ToolResult, ToolUseContext


class AskUserQuestionTool(Tool):
    name = "AskUserQuestion"
    description = (
        "向用户提问并提供预设答案。 "
        "当您需要用户澄清、确认或决策时使用此工具。 "
        "提供至少1个选项。用户始终可以选择'其他'输入自定义答案。\n\n"
        "用法:\n"
        "- 谨慎使用 - 仅在您确实需要用户输入才能继续时使用\n"
        "- 清晰地提出问题，并提供可操作的选项\n"
        "- 当选择风险较低时，优先做出合理假设而不是询问"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "要问用户的问题。应该清晰且具体。",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "预设答案列表（至少1个）。 "
                    "用户也可以通过选择'其他'输入自定义答案。"
                ),
            },
        },
        "required": ["question", "options"],
    }

    def permission_level(self, args: dict[str, Any]) -> str:
        return "read"

    def validate_input(self, args: dict[str, Any], context: ToolUseContext) -> str | None:
        given_keys = list(args.keys())
        options = args.get("options", [])
        if not isinstance(options, list) or len(options) < 1:
            return (
                f"错误：'options'参数必须是至少包含1个项目的列表。 "
                f"收到的参数：{given_keys}。 "
                f"使用<arg_key>question</arg_key><arg_value>您的问题</arg_value> "
                f"<arg_key>options</arg_key><arg_value>[\"选项A\", \"选项B\"]</arg_value>"
            )
        question = args.get("question", "")
        if not question or not question.strip():
            return (
                f"错误：'question'参数是必需的但未提供。 "
                f"收到的参数：{given_keys}。 "
                f"使用<arg_key>question</arg_key><arg_value>您的问题</arg_value> "
                f"<arg_key>options</arg_key><arg_value>[\"选项A\", \"选项B\"]</arg_value>"
            )
        return None

    async def call(self, args: dict[str, Any], context: ToolUseContext) -> ToolResult:
        import asyncio

        question: str = args["question"]
        options: list[str] = args["options"]

        if context.ask_user_callback is None:
            return ToolResult(
                data="此模式下没有用户交互功能。"
            )

        try:
            answer = await context.ask_user_callback(question, options)
        except asyncio.CancelledError:
            if context.abort_signal is not None:
                context.abort_signal.set()
            raise
        return ToolResult(data=answer)
