"""基于文本的工具调用解析器，用于没有原生工具调用支持的模型。

当模型不支持OpenAI tool_calls响应格式时，它可能仍会以其自己的格式输出工具调用。此模块从文本中提取这些工具调用并将其转换为ToolUseBlock对象。

支持的格式:
- ``hermes``: ``<tool_call>{"name": "...", "arguments": {...}}</tool_call>``
- ``glm``: ``<tool_call>Name<arg_key>k</arg_key><arg_value>v</arg_value></tool_call>``
- ``pivot``: ``<tool_use>{"name": "...", "input": {...}}</tool_use>``

每种格式都作为ToolCallFormat类实现，包含:
- ``parse(text)`` → 提取格式良好的工具调用
- ``detect_malformed(text)`` → 检测尝试但格式错误的工具调用
- ``format_error()`` → 返回给模型的错误反馈
- ``system_prompt(tool_schemas)`` → 返回系统提示的格式说明
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

MAX_TEXT_TOOL_RETRIES = 3


@dataclass
class ParsedToolCall:
    """从文本中提取的工具调用。"""
    name: str
    input: dict[str, Any]
    raw_match: str  # The full matched text (for removal from content)


@dataclass
class ParseResult:
    """解析文本以查找工具调用的结果。

    属性:
        tool_calls: 成功解析的工具调用。
        cleaned_text: 移除工具调用标记和思考标签后的文本。
        thinking: 提取的思考内容（来自``<think>``标签），或None。
        error: 如果非None，模型尝试了工具调用但格式错误。
            此消息应反馈给模型。
    """
    tool_calls: list[ParsedToolCall]
    cleaned_text: str
    thinking: str | None = None
    error: str | None = None


# ── 基类 ───────────────────────────────────────────────────────────────────────


class ToolCallFormat(ABC):
    """基于文本的工具调用格式解析器的基类。"""

    @abstractmethod
    def parse(self, text: str) -> list[ParsedToolCall]:
        """从文本中提取格式良好的工具调用。"""
        ...

    @abstractmethod
    def detect_malformed(self, text: str) -> bool:
        """检测尝试但格式错误的工具调用。

        如果文本包含类似工具调用但未干净解析的块，则返回True，否则返回False。
        """
        ...

    @abstractmethod
    def format_error(self) -> str:
        """返回要反馈给模型的错误消息。

        包含预期格式和示例。故意不回显模型自身的输出——
        这会混淆模型，使其无法区分自己的消息和工具反馈。
        """
        ...

    @abstractmethod
    def system_prompt(self, tool_schemas: list[dict]) -> str:
        """返回此格式的系统提示指令。"""
        ...


# ── 格式：hermes ───────────────────────────────────────────────────────────


_HERMES_PATTERN = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL,
)

# 松散模式：需要同时有开始和结束标签。在散文中提到的裸<tool_call>
# （例如模型为先前错误道歉并引用标签时）不得触发格式错误检测——
# 这会导致自我持续的重试循环，其中每条错误消息都会被引用回来。
_HERMES_LOOSE_PATTERN = re.compile(
    r"<tool_call>.*?</tool_call>",
    re.DOTALL,
)


class HermesFormat(ToolCallFormat):
    """Hermes/Qwen格式：``<tool_call>{"name": ..., "arguments": ...}</tool_call>``"""

    def parse(self, text: str) -> list[ParsedToolCall]:
        results = []
        for match in _HERMES_PATTERN.finditer(text):
            try:
                data = json.loads(match.group(1))
                name = data.get("name", "")
                arguments = data.get("arguments", data.get("input", {}))
                if name:
                    results.append(ParsedToolCall(
                        name=name, input=arguments, raw_match=match.group(0),
                    ))
            except json.JSONDecodeError:
                pass  # 在下方检测为格式错误
        return results

    def detect_malformed(self, text: str) -> bool:
        for match in _HERMES_LOOSE_PATTERN.finditer(text):
            if not _HERMES_PATTERN.match(match.group(0)):
                return True
        return False

    def format_error(self) -> str:
        return (
            "Found <tool_call> block but content is not valid.\n\n"
            "Expected format:\n"
            "<tool_call>\n"
            '{"name": "tool_name", "arguments": {"param": "value"}}\n'
            "</tool_call>\n\n"
            "Example:\n"
            "<tool_call>\n"
            '{"name": "Read", "arguments": {"file_path": "/path/to/file.py"}}\n'
            "</tool_call>\n\n"
            "Please retry with the correct format."
        )

    def system_prompt(self, tool_schemas: list[dict]) -> str:
        tools_json = json.dumps(tool_schemas, indent=2)
        return (
            "\n\n# Tool Calling\n\n"
            "You have access to the following tools:\n"
            f"<tools>\n{tools_json}\n</tools>\n\n"
            "To call a tool, output a JSON object inside <tool_call> tags:\n"
            "<tool_call>\n"
            '{"name": "tool_name", "arguments": {"param": "value"}}\n'
            "</tool_call>\n\n"
            "You may call multiple tools by outputting multiple <tool_call> blocks.\n"
            "After a tool call, wait for the result before continuing."
        )


# ── 格式：glm ──────────────────────────────────────────────────────────────


_GLM_PATTERN = re.compile(
    # 结束标签</tool_call>是必需的。没有结束标签的部分中间匹配
    # 过去被解析为完整工具调用并以截断参数执行。
    r"<tool_call>(\w+)((?:<arg_key>.*?</arg_key><arg_value>.*?</arg_value>)+)</tool_call>",
    re.DOTALL,
)

_GLM_ARG_PATTERN = re.compile(
    r"<arg_key>(.*?)</arg_key><arg_value>(.*?)</arg_value>",
    re.DOTALL,
)

# 松散模式：需要同时有开始和结束标签，这样散文中提到的裸<tool_call>
# 不会触发格式错误检测。
_GLM_LOOSE_PATTERN = re.compile(
    r"<tool_call>.*?</tool_call>",
    re.DOTALL,
)


class GLMFormat(ToolCallFormat):
    """GLM格式：``<tool_call>Name<arg_key>k</arg_key><arg_value>v</arg_value></tool_call>``"""

    def parse(self, text: str) -> list[ParsedToolCall]:
        results = []
        for match in _GLM_PATTERN.finditer(text):
            name = match.group(1)
            args_text = match.group(2)
            args = {}
            for arg_match in _GLM_ARG_PATTERN.finditer(args_text):
                key = arg_match.group(1).strip()
                value = arg_match.group(2).strip()
                args[key] = value
            if name:
                results.append(ParsedToolCall(
                    name=name, input=args, raw_match=match.group(0),
                ))
        return results

    def detect_malformed(self, text: str) -> bool:
        strict_matches = {m.group(0) for m in _GLM_PATTERN.finditer(text)}
        for match in _GLM_LOOSE_PATTERN.finditer(text):
            if match.group(0) not in strict_matches:
                return True
        return False

    def format_error(self) -> str:
        return (
            "Found <tool_call> block but format is incorrect.\n\n"
            "Expected format:\n"
            "<tool_call>ToolName"
            "<arg_key>parameter_name</arg_key>"
            "<arg_value>parameter_value</arg_value>"
            "</tool_call>\n\n"
            "Example:\n"
            "<tool_call>Bash"
            "<arg_key>command</arg_key>"
            "<arg_value>ls -la</arg_value>"
            "</tool_call>\n\n"
            "Please retry with the correct format."
        )

    def system_prompt(self, tool_schemas: list[dict]) -> str:
        tools_desc = "\n".join(
            f"- {t['function']['name']}: {t['function']['description']}"
            for t in tool_schemas
        )
        return (
            "\n\n# Available Tools\n\n"
            f"{tools_desc}\n\n"
            "Use <tool_call> tags to call tools with this exact format:\n"
            "<tool_call>ToolName"
            "<arg_key>param</arg_key>"
            "<arg_value>value</arg_value>"
            "</tool_call>"
        )


# ── 格式：pivot ─────────────────────────────────────────────────────────────


_ALAN_PATTERN = re.compile(
    r"<tool_use>\s*(\{.*?\})\s*</tool_use>",
    re.DOTALL,
)

# 松散模式：需要同时有开始和结束标签，这样散文中提到的裸<tool_use>
# 不会触发格式错误检测。
_ALAN_LOOSE_PATTERN = re.compile(
    r"<tool_use>.*?</tool_use>",
    re.DOTALL,
)


class PivotFormat(ToolCallFormat):
    """Alan格式：``<tool_use>{"name": ..., "input": ...}</tool_use>``"""

    def parse(self, text: str) -> list[ParsedToolCall]:
        results = []
        for match in _ALAN_PATTERN.finditer(text):
            try:
                data = json.loads(match.group(1))
                name = data.get("name", "")
                input_data = data.get("input", data.get("arguments", {}))
                if name:
                    results.append(ParsedToolCall(
                        name=name, input=input_data, raw_match=match.group(0),
                    ))
            except json.JSONDecodeError:
                pass
        return results

    def detect_malformed(self, text: str) -> bool:
        for match in _ALAN_LOOSE_PATTERN.finditer(text):
            if not _ALAN_PATTERN.match(match.group(0)):
                return True
        return False

    def format_error(self) -> str:
        return (
            "Found <tool_use> block but content is not valid.\n\n"
            "Expected format:\n"
            "<tool_use>\n"
            '{"name": "tool_name", "input": {"param": "value"}}\n'
            "</tool_use>\n\n"
            "Example:\n"
            "<tool_use>\n"
            '{"name": "Read", "input": {"file_path": "/path/to/file.py"}}\n'
            "</tool_use>\n\n"
            "Please retry with the correct format."
        )

    def system_prompt(self, tool_schemas: list[dict]) -> str:
        tools_json = json.dumps(tool_schemas, indent=2)
        return (
            "\n\n# Tool Calling\n\n"
            "You have access to the following tools:\n"
            f"<tools>\n{tools_json}\n</tools>\n\n"
            "To call a tool, output a JSON object inside <tool_use> tags:\n"
            "<tool_use>\n"
            '{"name": "tool_name", "input": {"param": "value"}}\n'
            "</tool_use>\n\n"
            "You may call multiple tools by outputting multiple <tool_use> blocks.\n"
            "After a tool call, wait for the result before continuing."
        )


# ── 注册表 ─────────────────────────────────────────────────────────────────


FORMATS: dict[str, ToolCallFormat] = {
    "hermes": HermesFormat(),
    "glm": GLMFormat(),
    "pivot": PivotFormat(),
}


def get_format(name: str) -> ToolCallFormat:
    """通过名称获取ToolCallFormat。

    引发:
        ValueError: 如果格式名称无法识别。
    """
    fmt = FORMATS.get(name)
    if fmt is None:
        raise ValueError(f"Unknown tool call format: {name!r}. Supported: {list(FORMATS.keys())}")
    return fmt


# ── 公共 API ───────────────────────────────────────────────────────────────


def _extract_thinking(text: str) -> tuple[str | None, str]:
    """从``<think>...</think>``标签中提取思考内容。

    返回(thinking_text, remaining_text)。
    如果未找到思考标签，则返回(None, original_text)。
    """
    # 处理<think>...</think>和只有</think>（开始标签有时缺失）
    import re
    match = re.search(r"<think>(.*?)</think>", text, re.DOTALL)
    if match:
        thinking = match.group(1).strip()
        remaining = text[:match.start()] + text[match.end():]
        return (thinking or None, remaining.strip())

    # 处理没有开始标签的</think>（模型有时只是关闭）
    if "</think>" in text:
        parts = text.split("</think>", 1)
        thinking = parts[0].strip()
        remaining = parts[1].strip() if len(parts) > 1 else ""
        return (thinking or None, remaining)

    return (None, text.strip())


def extract_tool_calls_from_text(
    text: str,
    format: str = "hermes",
) -> ParseResult:
    """从模型文本输出中提取工具调用。

    返回一个ParseResult，包含:
    - ``tool_calls``: 成功解析的工具调用
    - ``cleaned_text``: 移除标记后的文本
    - ``error``: 如果非None，模型尝试了工具调用但使用了错误的格式。
      此消息应作为工具结果错误发送回去，以便模型可以重试。
    """
    fmt = get_format(format)

    # 尝试严格解析
    tool_calls = fmt.parse(text)
    cleaned = text
    for tc in tool_calls:
        cleaned = cleaned.replace(tc.raw_match, "")
    thinking, cleaned = _extract_thinking(cleaned)

    if tool_calls:
        return ParseResult(tool_calls=tool_calls, cleaned_text=cleaned, thinking=thinking)

    # 没有有效的工具调用 - 检查格式错误的尝试
    if fmt.detect_malformed(text):
        thinking, cleaned = _extract_thinking(text)
        return ParseResult(tool_calls=[], cleaned_text=cleaned, thinking=thinking, error=fmt.format_error())

    # 完全没有工具调用尝试 - 正常文本响应
    thinking, cleaned = _extract_thinking(text)
    return ParseResult(tool_calls=[], cleaned_text=cleaned, thinking=thinking)


def get_tool_format_system_prompt(format: str, tool_schemas: list[dict]) -> str:
    """生成基于文本的工具调用的系统提示指令。"""
    return get_format(format).system_prompt(tool_schemas)
