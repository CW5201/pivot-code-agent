"""技能工具 - 模型调用的技能执行。

允许模型在识别出符合技能``when_to_use``描述的情况时
主动调用已发现的技能。
"""

from typing import Any, Literal

from pivotcode.tools.base import Tool, ToolResult, ToolUseContext


class SkillTool(Tool):
    """按名称执行技能（提示模板）。"""

    def __init__(self, skill_registry):
        # 避免循环导入 —— 注册表在初始化时传入
        self._registry = skill_registry

    @property
    def name(self) -> str:
        return "Skill"

    @property
    def description(self) -> str:
        return (
            "按名称执行技能（可重用提示模板）。 "
            "技能是在"
            ".pivot/skills/或~/.pivot/skills/中定义的基于markdown的工作流配方。使用/skill list查看可用技能。"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "要调用的技能名称",
                },
                "args": {
                    "type": "string",
                    "description": "传递给技能的可选参数（替换模板中的$ARGUMENTS）",
                },
            },
            "required": ["skill"],
        }

    async def call(self, args: dict[str, Any], context: ToolUseContext) -> ToolResult:
        skill_name = args["skill"]
        skill_args = args.get("args", "")

        skill = self._registry.get(skill_name)
        if skill is None:
            available = ", ".join(s.name for s in self._registry.list_all())
            return ToolResult(
                data=f"未知技能：{skill_name!r}。可用技能：{available or '无'}",
                is_error=True,
            )

        expanded = self._registry.expand(skill_name, skill_args)

        # 如果适用，构建带有工具限制提示的响应
        parts = [f'<skill-prompt name="{skill_name}">']
        if skill.allowed_tools:
            tools_str = ", ".join(skill.allowed_tools)
            parts.append(
                f"重要：执行此技能时，您只能使用"
                f"以下工具：{tools_str}"
            )
        parts.append(expanded)
        parts.append("</skill-prompt>")

        return ToolResult(data="\n".join(parts))

    def permission_level(self, args: dict[str, Any]) -> Literal["read", "write", "exec"]:
        return "read"  # 加载提示模板是只读的
