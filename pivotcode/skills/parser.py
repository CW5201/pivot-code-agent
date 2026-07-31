"""SKILL.md 解析器——YAML frontmatter + markdown 正文提取。

技能是带有 YAML frontmatter 的 markdown 文件，用于定义可复用的
提示模板。本模块负责将它们解析为 SkillDefinition 对象。
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# 用于将 YAML frontmatter 从 markdown 正文中分隔开的正则。
# 匹配：---<换行><yaml><换行>---<换行><body>
_FRONTMATTER_RE = re.compile(
    r"\A\s*---\s*\n(.*?)\n---\s*\n(.*)",
    re.DOTALL,
)


@dataclass
class SkillDefinition:
    """从 SKILL.md 文件解析出的技能定义。"""

    name: str  # 技能名（用于 /skill <name>）
    description: str  # 触发短语 / 面向模型的摘要
    body: str  # markdown 正文（提示模板）
    source_path: str  # SKILL.md 的绝对路径（或 "<builtin>"）
    allowed_tools: list[str] | None = None  # 工具限制模式（None = 全部）
    argument_hint: str | None = None  # 例如 "[environment]"
    when_to_use: str | None = None  # 面向模型自动调用的详细指引
    context: str = "inline"  # 目前仅支持 "inline"
    version: str | None = None


def parse_skill_file(path: str) -> SkillDefinition | None:
    """将 SKILL.md 文件解析为 SkillDefinition。

    如果文件无法读取，或 frontmatter 无效/缺失，则返回 None。
    解析出错时记录警告但绝不抛出异常。
    """
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Failed to read skill file %s: %s", path, exc)
        return None

    return parse_skill_content(content, source_path=path)


def parse_skill_content(content: str, *, source_path: str = "<string>") -> SkillDefinition | None:
    """将技能内容（frontmatter + 正文）解析为 SkillDefinition。

    便于在没有磁盘文件的情况下进行测试。
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        # 明确提示：缺少 frontmatter 是新手最常见的错误。
        logger.warning(
            "Skill %s: no YAML frontmatter found. "
            "The file must start with a `---` block defining at least "
            "`name:` and `description:`.",
            source_path,
        )
        return None

    yaml_text = match.group(1)
    body = match.group(2).strip()

    try:
        meta = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        # 明确提示——否则首次编写技能的人会耗费数小时排查。
        logger.warning(
            "Skill %s failed to load: invalid YAML frontmatter (%s)",
            source_path, exc,
        )
        return None

    if not isinstance(meta, dict):
        logger.warning(
            "Skill %s failed to load: frontmatter is not a mapping "
            "(got %s). The `---` block should contain key: value pairs.",
            source_path, type(meta).__name__,
        )
        return None

    # 必填字段
    name = meta.get("name")
    description = meta.get("description")
    if not name or not description:
        logger.warning(
            "Skill %s missing required fields (name=%r, description=%r). "
            "Add both to the frontmatter.",
            source_path, name, description,
        )
        return None

    # 可选字段：allowed_tools 若存在则必须为 list[str]。
    # 为方便起见接受单个字符串（常见的 YAML 简写），
    # 但拒绝 dict / 嵌套 list / 非字符串项，否则它们会被静默地
    # 变成 str([...]) 从而什么也过滤不到。
    allowed_tools = meta.get("allowed-tools") or meta.get("allowed_tools")
    if allowed_tools is not None:
        if isinstance(allowed_tools, str):
            allowed_tools = [allowed_tools]
        elif isinstance(allowed_tools, list) and all(
            isinstance(x, str) for x in allowed_tools
        ):
            pass  # 已经是正确的形状。
        else:
            logger.warning(
                "Skill %s: `allowed-tools` must be a list of tool-name strings "
                "(or a single tool name), got %r. Skill ignored.",
                source_path, allowed_tools,
            )
            return None

    return SkillDefinition(
        name=str(name),
        description=str(description),
        body=body,
        source_path=source_path,
        allowed_tools=allowed_tools,
        argument_hint=meta.get("argument-hint") or meta.get("argument_hint"),
        when_to_use=meta.get("when_to_use") or meta.get("when-to-use"),
        context=meta.get("context", "inline"),
        version=meta.get("version"),
    )
