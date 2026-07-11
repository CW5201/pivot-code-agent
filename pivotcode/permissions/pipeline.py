"""权限决策管线。

解析某个工具调用应当被允许、拒绝，还是提示用户确认。
该管线依次检查允许规则、拒绝规则、模式默认值，并在需要显式
批准时回退到用户的 ``ask_callback``。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from pivotcode.permissions.context import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
    PermissionRule,
    ToolPermissionContext,
)
from pivotcode.tools.base import Tool, ToolUseContext

logger = logging.getLogger(__name__)

# 用户提示回调的类型
PermissionPromptFn = Callable[[str, str, dict], Awaitable[PermissionBehavior]]
# 参数：tool_name、description_message、tool_input -> 返回 allow/deny


def check_rule_match(
    rules: list[PermissionRule],
    tool: Tool,
    input: dict,
) -> PermissionRule | None:
    """找到第一条匹配该工具 + 输入的规则。

    一条规则在以下条件满足时匹配：
    - rule.tool_name 与工具名匹配（精确匹配），并且
    - rule.rule_content 为 None（通配匹配），或者
    - rule.rule_content 与相关输入值的前缀匹配
      （例如对 Bash 而言，rule_content="git *" 匹配以 "git " 开头的命令）
    """
    for rule in rules:
        if not tool.matches_name(rule.tool_name):
            continue

        # 通配规则（无内容过滤器）——匹配任意输入
        if rule.rule_content is None:
            return rule

        rule_pattern = rule.rule_content.rstrip("*").rstrip()

        # 将模式路由到语义上属于工具“目标”的字段，按工具区分。
        # 此前我们扫描了输入字典中的每一个字符串值——一条形如
        # `Read: "config*"` 的规则会匹配被强制转为字符串的
        # `limit="config_limit"`，这让用户感到意外。
        _TOOL_FIELD_MAP = {
            "Bash": "command",
            "Read": "file_path",
            "Write": "file_path",
            "Edit": "file_path",
            "Glob": "pattern",
            "Grep": "pattern",
            "WebFetch": "url",
        }
        target_field = _TOOL_FIELD_MAP.get(tool.name)

        if target_field is not None:
            value = input.get(target_field)
            if isinstance(value, str) and (
                value == rule_pattern
                or value.startswith(rule_pattern + " ")
            ):
                return rule
        else:
            # 未知工具——回退为扫描所有字符串值，以便
            # 带有允许规则的用户自定义工具仍可正常工作。
            for value in input.values():
                if isinstance(value, str) and (
                    value == rule_pattern
                    or value.startswith(rule_pattern + " ")
                ):
                    return rule

    return None


def get_deny_rule(
    context: ToolPermissionContext,
    tool: Tool,
) -> PermissionRule | None:
    """检查工具是否被全面拒绝。

    查找匹配工具名（通配或特定）的拒绝规则。
    """
    for rule in context.deny_rules:
        if tool.matches_name(rule.tool_name):
            return rule
    return None


def _mode_allows(mode: PermissionMode, level: str) -> bool:
    """检查权限模式是否允许某个权限级别而无需询问。

    | 模式   | 读   | 写   | 执行 |
    |--------|------|-------|------|
    | yolo   | 是   | 是   | 是   |
    | edit   | 是   | 是   | 否   |
    | safe   | 是   | 否   | 否   |
    """
    if mode == PermissionMode.YOLO:
        return True
    if mode == PermissionMode.EDIT:
        return level in ("read", "write")
    if mode == PermissionMode.SAFE:
        return level == "read"
    return False


async def check_permissions(
    tool: Tool,
    input: dict,
    context: ToolUseContext,
    permission_context: ToolPermissionContext,
    *,
    prompt_user: PermissionPromptFn | None = None,
) -> PermissionResult:
    """运行权限决策管线。

    步骤 1：基于规则的检查（拒绝规则、询问规则、工具专属规则）
    步骤 2：模式检查（yolo/edit/safe × read/write/exec）
    步骤 3：Hooks（目前为透传占位——真正的 hook 在
            pivotcode/hooks/registry.py 中，并从 run_tool_use 触发）
    步骤 4：分类器（为未来的 ML 自动允许/拒绝层保留）
    步骤 5：用户提示（若以上全部给出“询问”）
    """

    # ── 步骤 1：基于规则的检查 ──────────────────────────────────────────

    # 1a. 先检查拒绝规则
    deny_rule = check_rule_match(permission_context.deny_rules, tool, input)
    if deny_rule is not None:
        logger.info("Permission denied by rule: %s (source=%s)", deny_rule.tool_name, deny_rule.source)
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"Tool '{tool.name}' denied by rule from {deny_rule.source}",
        )

    # 1b. 检查询问规则
    ask_rule = check_rule_match(permission_context.ask_rules, tool, input)
    if ask_rule is not None:
        logger.debug("Ask rule matched for tool '%s' (source=%s)", tool.name, ask_rule.source)
        # 暂不返回——下放到步骤 5（用户提示）
        # 但标记我们需要询问
        must_ask = True
    else:
        must_ask = False

    # 注意：tool.validate_input() 在 check_permissions() 之前已由
    # run_tool_use() 调用，因此这里不再重复。

    # ── 步骤 2：模式检查 ─────────────────────────────────────────────────

    level = tool.permission_level(input)

    if not must_ask and _mode_allows(permission_context.mode, level):
        logger.debug("Permission allowed by mode '%s' for tool '%s' (level=%s)",
                      permission_context.mode.value, tool.name, level)
        return PermissionResult(behavior=PermissionBehavior.ALLOW)

    # 同时检查显式的允许规则（针对特定工具覆盖模式）
    if not must_ask:
        allow_rule = check_rule_match(permission_context.allow_rules, tool, input)
        if allow_rule is not None:
            logger.debug(
                "Permission allowed by rule for tool '%s' (source=%s)",
                tool.name,
                allow_rule.source,
            )
            return PermissionResult(behavior=PermissionBehavior.ALLOW)

    # ── 步骤 3：Hooks（占位；真正的 hook 从 run_tool_use 触发） ──────────

    hook_result = PermissionBehavior.PASSTHROUGH
    if hook_result not in (PermissionBehavior.PASSTHROUGH,):
        return PermissionResult(behavior=hook_result)

    # ── 步骤 4：分类器（为未来的 ML 自动允许/拒绝保留） ────

    classifier_result = PermissionBehavior.PASSTHROUGH
    if classifier_result not in (PermissionBehavior.PASSTHROUGH,):
        return PermissionResult(behavior=classifier_result)

    # ── 步骤 5：用户提示 ────────────────────────────────────────────────

    # 如果应当避免提示（例如后台 agent），则默认拒绝
    if permission_context.should_avoid_prompts:
        logger.info("Avoiding prompt for tool '%s' (background agent)", tool.name)
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"Tool '{tool.name}' requires permission but prompts are disabled",
        )

    if prompt_user is not None:
        description = f"Tool '{tool.name}' wants to execute with input: {input}"
        user_decision = await prompt_user(tool.name, description, input)
        logger.info("User decision for tool '%s': %s", tool.name, user_decision.value)

        if user_decision == PermissionBehavior.ALLOW:
            return PermissionResult(behavior=PermissionBehavior.ALLOW)
        elif user_decision == PermissionBehavior.DENY:
            return PermissionResult(
                behavior=PermissionBehavior.DENY,
                message="Denied by user",
            )

    # 没有提示回调——返回 ASK，让调用方知道需要提示
    return PermissionResult(behavior=PermissionBehavior.ASK)
