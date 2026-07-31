"""技能发现——扫描目录以查找 SKILL.md 文件。

发现来源（优先级高者胜出）：
1. 项目技能——.pivot/skills/<name>/SKILL.md
2. 用户技能——~/.pivot/skills/<name>/SKILL.md
3. 内置技能——builtin.py 中的 Python 字典
"""

import logging
from pathlib import Path

from pivotcode.skills.builtin import BUILTIN_SKILLS
from pivotcode.skills.parser import SkillDefinition, parse_skill_file

logger = logging.getLogger(__name__)

SKILL_FILENAME = "SKILL.md"


def _scan_skills_dir(skills_dir: Path) -> dict[str, SkillDefinition]:
    """扫描某个技能目录以查找 SKILL.md 文件。

    期望的目录结构：skills_dir/<name>/SKILL.md
    返回 {name: SkillDefinition}。
    """
    results: dict[str, SkillDefinition] = {}

    if not skills_dir.is_dir():
        return results

    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_file = child / SKILL_FILENAME
        if not skill_file.is_file():
            continue

        skill = parse_skill_file(str(skill_file))
        if skill is None:
            continue

        # 若目录名与 frontmatter 中的 name 不同，以目录名为规范名
        dir_name = child.name
        if skill.name != dir_name:
            logger.debug(
                "Skill dir name %r differs from frontmatter name %r, using frontmatter",
                dir_name, skill.name,
            )

        if skill.name in results:
            logger.debug("Duplicate skill %r in %s, keeping first", skill.name, skills_dir)
        else:
            results[skill.name] = skill

    return results


def discover_skills(cwd: str) -> dict[str, SkillDefinition]:
    """从所有来源发现所有可用的技能。

    优先级（高者胜出）：项目 > 用户 > 内置。
    与内置同名的项目技能会替换掉内置技能。
    """
    # 从内置技能开始（优先级最低）
    skills: dict[str, SkillDefinition] = dict(BUILTIN_SKILLS)

    # 用户技能（~/.pivot/skills/）覆盖内置技能
    user_skills_dir = Path.home() / ".pivot" / "skills"
    user_skills = _scan_skills_dir(user_skills_dir)
    skills.update(user_skills)
    if user_skills:
        logger.info("Discovered %d user skill(s) from %s", len(user_skills), user_skills_dir)

    # 项目技能（.pivot/skills/）覆盖一切
    project_skills_dir = Path(cwd) / ".pivot" / "skills"
    project_skills = _scan_skills_dir(project_skills_dir)
    skills.update(project_skills)
    if project_skills:
        logger.info("Discovered %d project skill(s) from %s", len(project_skills), project_skills_dir)

    return skills
