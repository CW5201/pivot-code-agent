"""压缩（compact）提示模板。

提供 9 段式摘要提示、analysis/summary 标签结构，
以及 Layer C 压缩时使用的压缩后消息构造逻辑。
"""

from __future__ import annotations

import re

# -- 禁用工具的引导语 --
# 防止摘要生成器在压缩过程中尝试调用工具。
NO_TOOLS_PREAMBLE = (
    "CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.\n"
    "\n"
    "- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.\n"
    "- You already have all the context you need in the conversation above.\n"
    "- Tool calls will be REJECTED and will waste your only turn -- you will fail the task.\n"
    "- Your entire response must be plain text: an <analysis> block followed by a <summary> block.\n"
    "\n"
)

# -- 分析指令 --
DETAILED_ANALYSIS_INSTRUCTION = (
    "Before providing your final summary, wrap your analysis in <analysis> tags "
    "to organize your thoughts and ensure you've covered all necessary points. "
    "In your analysis process:\n"
    "\n"
    "1. Chronologically analyze each message and section of the conversation. "
    "For each section thoroughly identify:\n"
    "   - The user's explicit requests and intents\n"
    "   - Your approach to addressing the user's requests\n"
    "   - Key decisions, technical concepts and code patterns\n"
    "   - Specific details like:\n"
    "     - file names\n"
    "     - full code snippets\n"
    "     - function signatures\n"
    "     - file edits\n"
    "   - Errors that you ran into and how you fixed them\n"
    "   - Pay special attention to specific user feedback that you received, "
    "especially if the user told you to do something differently.\n"
    "2. Double-check for technical accuracy and completeness, addressing each "
    "required element thoroughly."
)

# -- 基础压缩提示（9 段式模板） --
BASE_COMPACT_PROMPT = f"""Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

{DETAILED_ANALYSIS_INSTRUCTION}

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Pay special attention to the most recent messages and include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. These are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests or really old requests that were already completed without confirming with the user first.
                       If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Summary of the changes made to this file, if any]
      - [Important Code Snippet]
   - [File Name 2]
      - [Important Code Snippet]
   - [...]

4. Errors and fixes:
    - [Detailed description of error 1]:
      - [How you fixed the error]
      - [User feedback on the error if any]
    - [...]

5. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]

6. All user messages:
    - [Detailed non tool use user message]
    - [...]

7. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the conversation so far, following this structure and ensuring precision and thoroughness in your response.

There may be additional summarization instructions provided in the included context. If so, remember to follow these instructions when creating the above summary. Examples of instructions include:
<example>
## Compact Instructions
When summarizing the conversation focus on typescript code changes and also remember the mistakes you made and how you fixed them.
</example>

<example>
# Summary instructions
When you are using compact - please focus on test output and code changes. Include file reads verbatim.
</example>
"""

# -- 禁用工具的尾部强调（在结尾处重申） --
NO_TOOLS_TRAILER = (
    "\n\nREMINDER: Do NOT call any tools. Respond with plain text only -- "
    "an <analysis> block followed by a <summary> block. "
    "Tool calls will be rejected and you will fail the task."
)


def get_compact_prompt(custom_instructions: str | None = None) -> str:
    """构建作为最终用户消息发送的压缩提示。

    包含禁用工具的引导语、9 段式摘要模板、
    可选的自定义指令，以及禁用工具的尾部强调。
    """
    prompt = NO_TOOLS_PREAMBLE + BASE_COMPACT_PROMPT

    if custom_instructions and custom_instructions.strip():
        prompt += f"\n\nAdditional Instructions:\n{custom_instructions}"

    prompt += NO_TOOLS_TRAILER
    return prompt


def format_compact_summary(raw_summary: str) -> str:
    """从大语言模型的回复中提取并清理摘要。

    会移除 <analysis> 草稿便签（它能提升摘要质量，
    但在摘要写好后就没有信息价值了），然后提取 <summary>
    内容。如果未找到 <summary> 标签，则回退为完整回复。
    """
    formatted = raw_summary

    # 移除 analysis 区块
    formatted = re.sub(r"<analysis>[\s\S]*?</analysis>", "", formatted)

    # 提取 summary 区块
    summary_match = re.search(r"<summary>([\s\S]*?)</summary>", formatted)
    if summary_match:
        content = summary_match.group(1).strip()
        formatted = f"Summary:\n{content}"
    else:
        # 回退：使用完整回复（此时 analysis 已被移除）
        formatted = formatted.strip()

    # 清理区块之间多余的空白
    formatted = re.sub(r"\n\n+", "\n\n", formatted)

    return formatted.strip()


def get_post_compact_message(
    summary: str,
    transcript_path: str | None = None,
    memory_mode: str = "on",
) -> str:
    """构建用于替换对话的压缩后用户消息。

    这是模型在压缩后看到的内容。它包含格式化的摘要，
    以及无缝继续对话的指令。
    """
    formatted_summary = format_compact_summary(summary)

    message = (
        "This session is being continued from a previous conversation that "
        "ran out of context. The summary below covers the earlier portion "
        "of the conversation.\n\n"
        f"{formatted_summary}"
    )

    if transcript_path:
        message += (
            f"\n\nIf you need specific details from before compaction "
            f"(like exact code snippets, error messages, or content you generated), "
            f"read the full transcript at: {transcript_path}"
        )

    message += (
        "\n\nContinue the conversation from where it left off without "
        "asking the user any further questions. Resume directly -- do not "
        "acknowledge the summary, do not recap what was happening, do not "
        "preface with \"I'll continue\" or similar. Pick up the last task "
        "as if the break never happened."
    )

    return message


def get_post_compact_notification(memory_mode: str = "on") -> str:
    """构建压缩后注入的 system-reminder 通知。

    告知模型重新读取文件，而不是依赖先前的上下文。
    """
    parts = [
        "<system-reminder>",
        "Your conversation was compacted. Earlier tool results and messages "
        "have been summarized. Re-read files you need with the Read tool "
        "rather than relying on earlier context.",
    ]
    if memory_mode != "off":
        parts.append("If memory is enabled, check your memory files.")
    parts.append("</system-reminder>")
    return "\n".join(parts)
