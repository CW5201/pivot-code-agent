"""工具系统基础类型。

Pivot Code中的每个工具都实现了Tool抽象基类。
"""
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ToolResult:
    """工具执行结果。"""
    data: Any  # 工具的输出（通常是str）
    is_error: bool = False
    # 在结果后注入的附加消息
    new_messages: list = field(default_factory=list)


@dataclass
class ToolUseContext:
    """传递给每个工具执行的上下文。
    携带工具调用所需的所有状态。"""
    cwd: str
    messages: list  # 当前对话历史
    settings: dict = None  # type: ignore[assignment]  # 完整的设置字典（用于钩子等）
    abort_signal: Any = None  # asyncio.Event或类似对象
    agent_id: str | None = None  # 非空表示子代理
    verbose: bool = False
    ask_user_callback: Callable[[str, list[str]], Awaitable[str]] | None = None
    session_state: Any = None  # SessionState实例（用于AGT工具）


class Tool(ABC):
    """所有工具的抽象基类。

    子类声明``name``、``description``、``input_schema``并
    实现``call``。代理循环通过注册表查找工具，将
    其模式发送到模型，然后将tool_use块分派回``call``。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """规范工具名称。用于模式和tool_use块。"""
        ...

    @property
    def aliases(self) -> list[str]:
        """工具也响应的替代名称。

        返回:
            额外名称列表。默认值：空列表。
        """
        return []

    @property
    @abstractmethod
    def description(self) -> str:
        """在工具模式中显示给模型的散文描述。"""
        ...

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]:
        """工具输入参数的JSON模式（OpenAI兼容）。"""
        ...

    @abstractmethod
    async def call(self, args: dict[str, Any], context: ToolUseContext) -> ToolResult:
        """执行工具并给定参数。

        参数:
            args: 模型提供的已验证输入字典。
            context: 会话范围的状态（cwd、消息、中止信号等）。

        返回:
            一个:class:`ToolResult` — 使用``is_error=True``向模型
            表示失败而不引发异常。
        """
        ...

    def permission_level(self, args: dict[str, Any]) -> Literal["read", "write", "exec"]:
        """此调用的权限级别。

        - ``"read"``  — 只读，可以并发运行，始终允许
        - ``"write"`` — 修改文件，串行运行，在``safe``模式下需要权限
        - ``"exec"``  — 任意执行（Bash），串行运行，在``edit``和``safe``模式下都需要权限

        参数:
            args: 与传递给:meth:`call`相同的参数。

        返回:
            ``"read"``、``"write"``、``"exec"``之一。默认值：``"write"``。
        """
        return "write"

    def is_enabled(self) -> bool:
        """此工具当前是否可用。

        返回:
            ``True``表示向模型公开工具，``False``表示隐藏工具。
        """
        return True

    def validate_input(self, args: dict[str, Any], context: ToolUseContext) -> str | None:
        """验证JSON模式之外的输入。

        用于模式无法表达的语义检查（例如"file_path必须存在"，"options列表必须至少有1个项目"）。

        参数:
            args: 来自模型的输入字典。
            context: 会话状态。

        返回:
            人类可读的错误消息（作为工具结果发回给模型），
            或者如果输入有效则返回``None``。
        """
        return None

    @property
    def max_result_size_chars(self) -> int | float:
        """磁盘持久化前的最大结果大小（字符数）。

        返回:
            大小上限。使用``float('inf')``禁用磁盘持久化。
        """
        return 50_000

    def matches_name(self, name: str) -> bool:
        """检查此工具是否响应``name``（主名称或别名）。

        参数:
            name: 模型在其tool_use块中使用的名称。

        返回:
            如果名称匹配则返回``True``。
        """
        return name == self.name or name in self.aliases

    def to_schema(self) -> dict[str, Any]:
        """转换为API工具模式格式。

        如果工具尚未设置，则将``additionalProperties: false``注入到input_schema中。
        这使得API拒绝包含未知字段的调用，而不是静默丢弃它们——
        模型得到清晰的错误并在下一轮自我修正。
        如果工具确实接受开放式输入，可以通过在其自身模式中设置``additionalProperties: true``来覆盖。
        """
        schema = dict(self.input_schema)
        if schema.get("type") == "object" and "additionalProperties" not in schema:
            schema["additionalProperties"] = False
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }
