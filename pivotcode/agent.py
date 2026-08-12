"""PivotCodeAgent — Pivot Code 的主要公共接口。

查询 API（2x2 矩阵）::

    agent = PivotCodeAgent(model="openrouter/google/gemini-2.5-flash")

    answer = agent.query("Fix the bug")                  # 同步，文本
    events = agent.query_events("Fix the bug")            # 同步，事件列表
    answer = await agent.query_async("Fix the bug")       # 异步，文本
    async for e in agent.query_events_async("Fix bug"):   # 异步，事件流
        ...
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import queue
from collections.abc import AsyncGenerator, Callable
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pivotcode.api.cost_tracker import CostTracker
from pivotcode.hooks.handlers import on_session_end, on_session_start
from pivotcode.memory.memdir import (
    cleanup_old_scratchpads,
    ensure_memory_structure,
    get_global_memory_dir,
    get_memory_dir,
    load_global_memory_index,
    load_global_project_instructions,
    load_memory_index,
    load_project_instructions,
)
from pivotcode.memory.prompt import build_memory_section
from pivotcode.messages.factory import create_user_message
from pivotcode.messages.types import (
    AssistantMessage,
    AttachmentMessage,
    Message,
    StreamEvent,
    SystemMessage,
    Usage,
    UserMessage,
)
from pivotcode.permissions.context import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
    ToolPermissionContext,
)
from pivotcode.permissions.pipeline import check_permissions
from pivotcode.prompt.system_prompt import get_system_prompt
from pivotcode.providers.base import LLMProvider
from pivotcode.query.loop import QueryParams, query_loop
from pivotcode.session.session import (
    load_session_settings,
    save_session_settings,
)
from pivotcode.session.state import SessionState
from pivotcode.session.transcript import (
    load_transcript,
    record_transcript,
)
from pivotcode.settings import (
    SETTINGS_DEFAULTS,
    infer_backend,
    load_projects_settings_and_maybe_init,
    load_settings,
    save_settings,
    validate_setting,
)
from pivotcode.skills.registry import SkillRegistry
from pivotcode.tools.base import ToolUseContext
from pivotcode.tools.registry import get_enabled_tools
from pivotcode.tools.text_tool_parser import get_tool_format_system_prompt

logger = logging.getLogger(__name__)


class AgentState(str, Enum):
    """代理的生命周期状态。"""

    WAITING = "waiting"
    RUNNING = "running"
    ERROR = "error"


# ── 辅助函数 ──────────────────────────────────────────────────────────────────


def _ensure_pivot_gitignored(cwd: str) -> None:
    """确保 ``.pivot/`` 已列在 ``.gitignore`` 中。

    这对于 AGT 操作期间 ``git clean -fd`` 的安全性至关重要。
    如果没有这个，``git clean`` 会删除会话状态。
    """
    gitignore = Path(cwd) / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text()
        if ".pivot" in content:
            return  # 已存在
        # 追加
        if not content.endswith("\n"):
            content += "\n"
        content += ".pivot/\n"
        gitignore.write_text(content)
    else:
        gitignore.write_text(".pivot/\n")


# ── 后端解析 ──────────────────────────────────────────────────────────────


def _resolve_backend(
    backend: str | LLMProvider,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs: Any,
) -> LLMProvider:
    """将后端字符串（或预构建的 ``LLMProvider``）解析为代理可以流式传输的
    ``LLMProvider`` 实例。

    如果 *backend* 已经是 ``LLMProvider``，则原样返回——
    这是为想要自定义传输方式的用户提供的便捷通道。
    否则，在注册表中查找后端名称：

    - ``"auto"``             → ``LiteLLMProvider``（通用，前缀路由）。
    - ``"anthropic-native"`` → ``AnthropicProvider``（直接 SDK）。
    - ``"scripted"``         → ``ScriptedProvider``（测试用）。
    """
    if isinstance(backend, LLMProvider):
        return backend

    if model is None:
        raise ValueError(
            "No model configured. Set a model via:\n"
            "  - CLI: pivotcode --model <model_name>\n"
            "  - Settings: /settings-project model=<model_name>\n"
            "  - Constructor: PivotCodeAgent(model='<model_name>')"
        )

    name = backend.lower() if isinstance(backend, str) else backend

    if name == "auto":
        from pivotcode.providers.litellm_provider import LiteLLMProvider

        return LiteLLMProvider(
            model=model,
            api_key=api_key,
            api_base=base_url,
            **kwargs,
        )

    if name == "anthropic-native":
        from pivotcode.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key=api_key, model=model, base_url=base_url, **kwargs)

    if name == "scripted":
        # ``model="remote"`` 选择 HTTP 驱动的模拟后端；
        # 任何其他模型名称（或 None）使用内存中的 ScriptedProvider。
        if isinstance(model, str) and model.lower() == "remote":
            from pivotcode.providers.remote_scripted_provider import (
                RemoteScriptedProvider,
            )
            return RemoteScriptedProvider(**kwargs)
        from pivotcode.providers.scripted_provider import ScriptedProvider

        return ScriptedProvider(**kwargs)

    raise ValueError(
        f"Unknown backend '{backend}'. "
        f"Supported: 'auto', 'anthropic-native', 'scripted', "
        f"or pass an LLMProvider instance."
    )


def _create_provider_from_settings(settings: dict[str, Any], **extra) -> LLMProvider:
    """根据 *settings* 创建 ``LLMProvider`` 实例。

    由 ``__init__`` 和 ``update_session_setting`` 使用，
    当后端相关键在会话期间发生变化时。
    """
    return _resolve_backend(
        settings.get("backend", "auto"),
        model=settings.get("model"),
        api_key=settings.get("api_key"),
        base_url=settings.get("base_url"),
        **extra,
    )


# ── 代理主体 ────────────────────────────────────────────────────────────────


class PivotCodeAgent:
    """Pivot Code 会话的主接口。

    所有配置直接传递——不需要单独的配置对象。

    参数
    ----------
    backend : str 或 LLMProvider，可选
        传输后端（高级选项）。可以是字符串
        （``"auto"`` — 通用 LiteLLM 传输；
        ``"anthropic-native"`` — 直接 Anthropic SDK，支持 cache_control、
        思考和原生 tool_use；``"scripted"`` — 内部/测试用）
        或预构建的 ``LLMProvider`` 实例。未设置时，
        后端从 *model* 推断（纯 ``claude-*`` →
        ``"anthropic-native"``，其他 → ``"auto"``）。
    model : str，可选
        使用的模型。接受纯名称（``"gpt-4o"``、
        ``"claude-sonnet-4-6"``）或 LiteLLM 风格的 ``provider/model``
        前缀（``"ollama/llama3.1"``、
        ``"openrouter/google/gemini-2.5-pro"``）。
    api_key : str，可选
        API 密钥。如果为 None，则从环境变量读取。
    cwd : str，可选
        工作目录。默认为 ``os.getcwd()``。
    permission_mode : str
        权限模式：``"yolo"``、``"edit"``、``"safe"``。
    max_iterations_per_turn : int，可选
        每轮最大代理迭代次数。
    max_output_tokens : int，可选
        每个 LLM 响应的最大 token 数。
    session_id : str，可选
        显式会话 ID（由 CLI 或调用者预解析）。如果为 None 则自动生成。
    ask_callback : callable，可选
        用户提示的异步回调（权限问题、工具输入）。
        签名：``async (question: str, options: list[str]) -> str``。
        如果为 None，权限提示默认为拒绝。
    verbose : bool
        启用调试日志。
    provider : str 或 LLMProvider，可选
        *backend* 的已弃用别名。旧值会转换：
        ``"litellm"`` → ``"auto"``、``"anthropic"`` →
        ``"anthropic-native"``、``"scripted"`` → ``"scripted"``。
        发出 ``DeprecationWarning``；将在未来版本中移除。
    **provider_kwargs
        传递给后端构造函数的额外关键字参数
        （仅当 *backend* 为字符串时）。
    """

    def __init__(
        self,
        backend: str | LLMProvider | None = None,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        cwd: str | None = None,
        permission_mode: str | None = None,
        max_iterations_per_turn: int | None = None,
        max_output_tokens: int | None = None,
        memory: str | None = None,
        tool_call_format: str | None = None,
        session_id: str | None = None,
        ask_callback: Callable | None = None,
        verbose: bool = False,
        extra_tools: list | None = None,
        custom_system_prompt: str | None = None,
        gui_label: str | None = None,
        programmatic: bool = False,
        tools: list | None = None,
        disabled_tools: list[str] | None = None,
        provider: str | LLMProvider | None = None,  # ``backend`` 的已弃用别名
        **provider_kwargs: Any,
    ) -> None:
        # 处理已弃用的 ``provider=`` 关键字参数。
        if provider is not None:
            import warnings

            if backend is not None:
                raise TypeError(
                    "Pass either 'backend=' or the deprecated 'provider='; "
                    "not both."
                )
            warnings.warn(
                "PivotCodeAgent(provider=...) is deprecated; "
                "use backend=... instead. Values map as: "
                "'litellm' -> 'auto', 'anthropic' -> 'anthropic-native', "
                "'scripted' -> 'scripted'.",
                DeprecationWarning,
                stacklevel=2,
            )
            if isinstance(provider, str):
                from pivotcode.settings import _LEGACY_PROVIDER_MAP

                backend = _LEGACY_PROVIDER_MAP.get(provider.lower(), provider)
            else:
                backend = provider
        self._gui_label = gui_label
        self._programmatic = programmatic

        self._cwd = cwd or os.getcwd()

        # 解析会话 ID
        if session_id:
            self._session_id = session_id
        else:
            self._session_id = uuid4().hex

        # 加载设置基础（项目或会话），用于与 CLI 覆盖合并
        if session_id:
            settings_base = load_session_settings(self._cwd, session_id)
            if not settings_base:
                settings_base = load_projects_settings_and_maybe_init(self._cwd)
        else:
            settings_base = load_projects_settings_and_maybe_init(self._cwd)

        # 合并：默认设置 <- 会话设置 <- 构造函数关键字参数（仅非 None 值）
        self._settings: dict[str, Any] = dict(SETTINGS_DEFAULTS)
        self._settings.update({k: v for k, v in settings_base.items()})

        # 构造函数接受 ``backend`` 下的 ``LLMProvider`` 实例。
        # 该实例无法被 JSON 序列化到设置中，因此我们
        # 将其单独保存，稍后直接传递给 ``_resolve_backend``。
        backend_instance: LLMProvider | None = None
        backend_setting: str | None = None
        if isinstance(backend, LLMProvider):
            backend_instance = backend
        elif backend is not None:
            backend_setting = backend

        constructor_overrides: dict[str, Any] = {
            "backend": backend_setting,
            "model": model,
            "api_key": api_key,
            "base_url": base_url,
            "permission_mode": permission_mode,
            "max_iterations_per_turn": max_iterations_per_turn,
            "max_output_tokens": max_output_tokens,
            "memory": memory,
            "tool_call_format": tool_call_format,
        }
        backend_explicit = backend_setting is not None or backend_instance is not None
        for k, v in constructor_overrides.items():
            if v is not None:
                self._settings[k] = v

        # 推断：如果调用者设置了 ``model`` 但未设置 ``backend``，
        # 为该模型选择正确的后端（纯 claude-* → 原生；其他 → 自动）。
        # 当传入 LLMProvider 实例时跳过——用户已经决定使用哪种传输方式。
        if backend_instance is None and not backend_explicit and model is not None:
            self._settings["backend"] = infer_backend(model)

        if verbose: # verbose=True 应覆盖；verbose=False（默认值）不应
            self._settings["verbose"] = True

        # 解析关键字段
        if backend_instance is not None:
            self._provider = backend_instance
        else:
            self._provider = _create_provider_from_settings(self._settings, **provider_kwargs)
        self._model = self._settings.get("model")
        self._permission_mode = self._settings.get("permission_mode", "edit")
        self._max_iterations_per_turn = self._settings.get("max_iterations_per_turn")
        self._max_output_tokens = self._settings.get("max_output_tokens")
        self._memory_mode: str = self._settings.get("memory") or "off"
        self._verbose = self._settings.get("verbose", False)

        # 会话状态（磁盘关联——所有持久状态存储于此）
        self._session = SessionState(
            session_id=self._session_id,
            cwd=self._cwd,
        )

        # 可选的主动钩子：想要知道会话 ID
        # 和工作目录的提供者（例如 remote-scripted 后端，它会将其
        # 待处理负载镜像到会话目录）可以实现
        # ``set_session_context(session_id, cwd)``。
        if hasattr(self._provider, "set_session_context"):
            self._provider.set_session_context(
                session_id=self._session_id, cwd=self._cwd,
            )

        # 成本跟踪器（定价逻辑，将总计委托给 SessionState）
        self._cost_tracker = CostTracker(session=self._session)

        # 最近完成的 API 调用的使用量。用于显示的
        # "对话: N / M" 数据（轮次后权威数据）以及调用前
        # 压缩估计的下限。在 /clear 时重置。
        # 恢复会话时从持久化的 SessionState 获取初始值，
        # 使恢复后的第一轮具有基于使用量的下限。
        self._last_usage = Usage(
            input_tokens=self._session.last_input_tokens,
            output_tokens=self._session.last_output_tokens,
            cache_read_input_tokens=self._session.last_cache_read_tokens,
            cache_creation_input_tokens=self._session.last_cache_write_tokens,
        )

        # 事件监听器（用于 FrontendBridge / GUI 集成）
        self._event_listeners: list[Callable] = []
        # LLM 视角回调（由 GUI 桥接设置以接收 api_messages 快照）
        self._llm_perspective_callback: Callable | None = None

        # 技能
        self._skill_registry = SkillRegistry(self._cwd)

        # 工具、中止、消息队列
        self._state = AgentState.WAITING
        self._messages: list[Message] = []
        from pivotcode.tools.builtin.skill_tool import SkillTool
        from pivotcode.tools.registry import get_programmatic_tool_set

        if tools is not None:
            base = list(tools)
        elif programmatic:
            base = get_programmatic_tool_set()
        else:
            base = get_enabled_tools()
            base.append(SkillTool(self._skill_registry))
        if disabled_tools:
            blocked = set(disabled_tools)
            base = [t for t in base if t.name not in blocked]
        if extra_tools:
            base.extend(extra_tools)
        self._tools = base
        self._custom_system_prompt = custom_system_prompt
        self._abort_event = asyncio.Event()
        self._message_queue: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._permission_context = ToolPermissionContext(
            mode=PermissionMode(self._permission_mode),
        )
        self._load_project_allow_rules()
        self._session_start_fired = False
        self._ask_callback = ask_callback
        # 活动技能工具过滤器（由 /skill 命令设置，轮次后清除）
        self._active_skill_filter: list[str] | None = None

        # 保存会话设置快照
        save_session_settings(self._cwd, self._session_id, self._settings)

        # 内存和草稿本设置
        if self._memory_mode != "off":
            ensure_memory_structure(self._cwd)

        self._scratchpad_dir = (
            Path(self._cwd) / ".pivot" / "sessions" / self._session_id / "scratchpad"
        )
        self._scratchpad_dir.mkdir(parents=True, exist_ok=True)

        max_scratch = self._settings.get("max_scratchpad_sessions", 5)
        cleanup_old_scratchpads(self._cwd, max_sessions=max_scratch)

        # 从上一个会话加载记录（如果恢复）
        if session_id:
            messages = _run_async_safe(load_transcript(session_id, cwd=self._cwd))
            if messages:
                self._messages = messages
                logger.info(
                    "Resumed session %s (%d messages)", session_id, len(messages)
                )
            # 触发旧版允许规则迁移（较旧的会话将规则存储在
            # state.json 中；访问属性会将其迁移到上面已加载的
            # 项目级存储中）。
            _ = self._session.allow_rules

    # ── 查询 API（2x2 矩阵：文本/事件 × 同步/异步） ───────────────────
    #
    #   |            | 同步（默认）          | 异步                          |
    #   |------------|----------------------|-------------------------------|
    #   | 文本       | query(msg) → str     | query_async(msg) → str        |
    #   | 事件       | query_events(msg)    | query_events_async(msg)       |
    #   |            | → list[Event]        | → AsyncGenerator[Event]       |

    def query(self, message: str) -> str:
        """发送消息并返回最终的助手文本。

        这是使用 Pivot Code 的最简单方式。阻塞直到完整轮次完成
        （包括工具执行）。

        示例::

            agent = PivotCodeAgent(model="gemini/gemini-2.5-flash")
            answer = agent.query("What files are in this project?")
            print(answer)
        """
        return _run_async(self._query_text_async(message))

    def query_events(self, message: str) -> list:
        """发送消息并返回完整的事件列表。

        阻塞直到完整轮次完成。返回所有事件
        （流式传输增量、工具调用、工具结果、最终消息）。

        示例::

            events = agent.query_events("Fix the bug")
            for event in events:
                print(type(event).__name__)
        """
        return _run_async(self._query_events_collect_async(message))

    async def query_async(self, message: str) -> str:
        """发送消息并返回最终的助手文本（异步）。

        类似 :meth:`query` 但非阻塞——用于异步代码内部
        （Web 服务器、异步脚本等）。

        示例::

            answer = await agent.query_async("Fix the bug")
            return {"answer": answer}
        """
        return await self._query_text_async(message)

    async def query_events_async(
        self, message: str
    ) -> AsyncGenerator[StreamEvent | Message, None]:
        """发送消息并生成流式事件（异步生成器）。

        用于向 UI、WebSocket 或自定义处理器进行实时流式传输。

        示例::

            async for event in agent.query_events_async("Fix the bug"):
                send_to_websocket(event)
        """
        if self._state == AgentState.RUNNING:
            raise RuntimeError(
                "Agent is already running. Use inject_message() to inject "
                "a message into the active loop."
            )

        self._state = AgentState.RUNNING
        self._abort_event.clear()

        # 首次触发 SessionStart 钩子
        if not self._session_start_fired:
            self._session_start_fired = True
            try:
                await on_session_start(
                    cwd=self._cwd,
                    session_id=self._session.session_id,
                    model=self._model,
                    settings=self._settings,
                )
            except Exception:
                logger.debug("SessionStart hook error (ignored)", exc_info=True)

            # 初始化 AGT 会话根（仅一次，在第一轮时）
            if not self._programmatic:
                self._init_agt_root()

        try:
            # --- 用户消息 ---
            user_msg = create_user_message(message)
            self._messages.append(user_msg)

            # --- 系统提示 ---
            mem_dir = get_memory_dir(self._cwd)
            global_mem_dir = get_global_memory_dir()
            memory_index = load_memory_index(cwd=self._cwd)
            global_memory_index = (
                None if self._programmatic else load_global_memory_index()
            )
            memory_section_text = build_memory_section(
                self._memory_mode,
                str(mem_dir),
                memory_index,
                global_memory_dir=str(global_mem_dir),
                global_memory_index=global_memory_index,
            )
            if self._programmatic:
                global_instructions = None
                project_instructions = None
            else:
                global_instructions = load_global_project_instructions()
                project_instructions = load_project_instructions(self._cwd)
            # 合并全局 + 项目指令（项目在冲突时优先）
            append_parts = [p for p in (global_instructions, project_instructions) if p]
            append_prompt = "\n\n".join(append_parts) if append_parts else None
            system_prompt, system_static_boundary = get_system_prompt(
                tools=self._tools,
                skills=self._skill_registry.list_all(),
                model=self._model,
                cwd=self._cwd,
                custom_prompt=self._custom_system_prompt,
                append_prompt=append_prompt,
                memory_section=memory_section_text,
                scratchpad_dir=str(self._scratchpad_dir),
            )

            # --- 基于文本的工具调用指令 ---
            tool_call_format = self._settings.get("tool_call_format")
            model_info = self._provider.get_model_info(self._model)
            if tool_call_format:
                tool_schemas = [
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.input_schema,
                        },
                    }
                    for t in self._tools
                    if t.is_enabled()
                ]
                system_prompt.append(
                    get_tool_format_system_prompt(tool_call_format, tool_schemas)
                )
                logger.info(
                    "Text-based tool calling enabled (format=%s, %d tools)",
                    tool_call_format, len(tool_schemas),
                )

            # --- 工具上下文 ---
            context = ToolUseContext(
                cwd=self._cwd,
                messages=self._messages,
                settings=self._settings,
                abort_signal=self._abort_event,
                ask_user_callback=self._ask_callback,
                session_state=self._session,
            )

            # --- 权限回调 ---
            # 将 check_permissions 与代理的权限上下文包装在一起。
            # 遵循 CC 的模式：canUseTool 每轮构建一次，
            # 并通过查询循环 -> 编排 -> 执行传递。
            _perm_ctx = self._permission_context
            _ask_cb = self._ask_callback
            _session = self._session

            # 可变容器，用于从提示传递自定义消息到结果
            _permission_custom_message: list[str | None] = [None]

            async def _prompt_user_permission(
                tool_name: str, description: str, tool_input: dict,
            ) -> PermissionBehavior:
                """通过 ask_callback 向用户请求权限。"""
                if _ask_cb is None:
                    return PermissionBehavior.DENY

                # 从命令前缀构建"始终允许"选项
                allow_always_label = None
                allow_always_pattern = None
                if tool_name == "Bash" and "command" in tool_input:
                    cmd = tool_input["command"]
                    prefix = cmd.split()[0] if cmd.strip() else ""
                    if prefix:
                        allow_always_pattern = prefix
                        allow_always_label = f'Allow always "{prefix} *" commands'

                options = ["Allow", "Deny"]
                if allow_always_label:
                    options.append(allow_always_label)

                try:
                    answer = await _ask_cb(
                        f"Allow {tool_name}?\n{description}",
                        options,
                    )
                except asyncio.CancelledError:
                    # 用户在权限提示处按了 Ctrl+C——信号
                    # 整个轮次中止，然后重新引发。
                    self._abort_event.set()
                    raise
                if answer == "Allow":
                    return PermissionBehavior.ALLOW
                if answer == "Deny":
                    return PermissionBehavior.DENY
                if answer == allow_always_label and allow_always_pattern:
                    # 为此前缀添加会话范围的允许规则
                    from pivotcode.permissions.context import PermissionRule
                    rule = PermissionRule(
                        tool_name="Bash",
                        rule_content=f"{allow_always_pattern} *",
                        behavior=PermissionBehavior.ALLOW,
                        source="project",
                    )
                    _perm_ctx.allow_rules.append(rule)
                    # 持久化到项目级存储（跨会话保留）
                    from pivotcode.permissions.project_rules import add_project_allow_rule
                    add_project_allow_rule({
                        "tool_name": rule.tool_name,
                        "rule_content": rule.rule_content,
                        "source": "project",
                    }, cwd=self._cwd)
                    logger.info("Added project allow rule: Bash(%s *)", allow_always_pattern)
                    return PermissionBehavior.ALLOW
                # 自定义文本——存储它以便模型看到用户的反馈
                _permission_custom_message[0] = answer
                return PermissionBehavior.DENY

            async def _permission_callback(
                tool, args, ctx,
            ) -> PermissionResult:
                _permission_custom_message[0] = None
                result = await check_permissions(
                    tool, args, ctx, _perm_ctx,
                    prompt_user=_prompt_user_permission,
                )
                if result.behavior == PermissionBehavior.DENY and _permission_custom_message[0]:
                    result.message = f"User response: {_permission_custom_message[0]}"
                return result

            # --- 查询循环 ---
            # 如果有活动则应用技能工具过滤器
            effective_tools = self._tools
            if self._active_skill_filter is not None:
                from pivotcode.skills.tool_filter import filter_tools_for_skill
                effective_tools = filter_tools_for_skill(self._tools, self._active_skill_filter)

            params = QueryParams(
                messages=self._messages,
                system_prompt=system_prompt,
                system_static_boundary=system_static_boundary,
                provider=self._provider,
                tools=effective_tools,
                context=context,
                cost_tracker=self._cost_tracker,
                model=self._model,
                max_iterations_per_turn=self._max_iterations_per_turn,
                max_output_tokens=self._max_output_tokens,
                abort_event=self._abort_event,
                message_queue=self._message_queue,
                memory_mode=self._memory_mode,
                settings=self._settings,
                permission_callback=_permission_callback,
                last_input_tokens_seed=self._last_usage.input_tokens,
                last_output_tokens_seed=self._last_usage.output_tokens,
                llm_perspective_callback=self._llm_perspective_callback,
            )

            async for event in query_loop(params):
                # 捕获最后一个最终助手消息的使用量，以便
                # 显示和下一次迭代的调用前估计可以使用它。
                if (
                    isinstance(event, AssistantMessage)
                    and not getattr(event, "hide_in_api", False)
                    and event.usage.input_tokens > 0
                ):
                    self._last_usage = event.usage
                if isinstance(
                    event,
                    (UserMessage, AssistantMessage, SystemMessage, AttachmentMessage),
                ) and not getattr(event, "hide_in_api", False):
                    if event is not user_msg:
                        self._messages.append(event)
                # 通知事件监听器（GUI 桥接等）
                for listener in self._event_listeners:
                    try:
                        await listener(event)
                    except Exception:
                        logger.debug("Event listener error", exc_info=True)
                yield event

            # 持久化记录
            await record_transcript(
                self._session.session_id, self._messages, cwd=self._cwd
            )

        except GeneratorExit:
            # 生成器被放弃（REPL 中的 Ctrl+C）——清理前保存状态
            logger.info("Turn interrupted by user")
            try:
                await record_transcript(
                    self._session.session_id, self._messages, cwd=self._cwd
                )
            except Exception:
                logger.debug("Failed to save state on interrupt", exc_info=True)
        except Exception:
            self._state = AgentState.ERROR
            logger.exception("Agent error")
            raise
        finally:
            self._state = AgentState.WAITING
            # 最佳努力刷新轮次边界状态。即使在取消时也运行，
            # 因为刷新是同步的（没有 await），
            # 但我们仍然用 try/except 包装，以便磁盘错误
            # 永远不会掩盖正在传播的实际异常。
            try:
                with self._session.batch():
                    self._session.turn_count += 1
                    self._session.last_input_tokens = self._last_usage.input_tokens
                    self._session.last_output_tokens = self._last_usage.output_tokens
                    self._session.last_cache_read_tokens = (
                        self._last_usage.cache_read_input_tokens
                    )
                    self._session.last_cache_write_tokens = (
                        self._last_usage.cache_creation_input_tokens
                    )
            except Exception as exc:
                logger.warning("Failed to persist turn-boundary state: %s", exc)
            # 轮次完成后清除活动技能过滤器
            self._active_skill_filter = None

    async def close(self) -> None:
        """触发 SessionEnd 钩子。会话结束时调用一次。"""
        try:
            await on_session_end(
                session_id=self._session.session_id,
                total_cost=self._session.total_cost_usd,
                turn_count=self._session.turn_count,
                settings=self._settings,
            )
        except Exception:
            logger.debug("SessionEnd hook error (ignored)", exc_info=True)
        self._session.close()

    # ── 允许规则持久化 ──────────────────────────────────────────────

    def _load_project_allow_rules(self) -> None:
        """从 ``.pivot/allow_rules.json`` 加载项目级允许规则。"""
        from pivotcode.permissions.context import PermissionRule
        from pivotcode.permissions.project_rules import load_project_allow_rules
        rules = load_project_allow_rules(self._cwd)
        for rule_data in rules:
            self._permission_context.allow_rules.append(
                PermissionRule(
                    tool_name=rule_data["tool_name"],
                    rule_content=rule_data.get("rule_content"),
                    behavior=PermissionBehavior.ALLOW,
                    source="project",
                )
            )
        if rules:
            logger.info("Loaded %d project allow rules", len(rules))

    def _init_agt_root(self) -> None:
        """初始化 AGT 会话根 SHA（仅一次，在会话开始时）。

        如果我们在 git 仓库中且 session_root_sha 尚未设置，
        记录 HEAD 作为此会话的起点。
        同时确保 ``.pivot/`` 被 gitignore（对于 AGT 移动操作
        期间 ``git clean`` 的安全性至关重要）。
        """
        if self._session.session_root_sha:
            return  # 已初始化（恢复的会话）
        try:
            from pivotcode.utils.env import is_git_repo
            if not is_git_repo(self._cwd):
                return

            # 确保 .pivot 被 gitignore（防止 git clean 删除会话数据）
            _ensure_pivot_gitignored(self._cwd)

            import subprocess
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self._cwd,
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                sha = result.stdout.strip()
                with self._session.batch():
                    self._session.session_root_sha = sha
                    self._session.agent_position_sha = sha
                    self._session.add_to_conv_path(sha)
                    self._session.record_commit_message_index(
                        sha, len(self._messages),
                    )
                logger.debug("AGT root initialized: %s", sha[:7])
        except Exception:
            logger.debug("AGT root init failed (non-critical)", exc_info=True)

    # ── 控制 API ────────────────────────────────────────────────────────────

    def add_event_listener(self, callback: Callable) -> None:
        """注册一个接收 query_events_async 中每个事件的回调。

        由 FrontendBridge/GUI 使用，用于观察事件而不消费
        生成器。用于编程式 GUI 使用::

            agent = PivotCodeAgent(...)
            gui = PivotGUI(agent)  # 内部调用 add_event_listener
        """
        self._event_listeners.append(callback)

    def remove_event_listener(self, callback: Callable) -> None:
        """移除先前注册的事件监听器。"""
        if callback in self._event_listeners:
            self._event_listeners.remove(callback)

    def inject_message(self, message: str) -> None:
        """在代理运行时注入消息。

        消息被排队，并在下一次循环迭代时被拾取。
        """
        self._message_queue.put(message)

    def abort(self) -> None:
        """通知代理尽快停止处理。"""
        self._abort_event.set()

    # ── 属性 ────────────────────────────────────────────────────────

    @property
    def state(self) -> AgentState:
        """当前的 :class:`AgentState`（``WAITING``、``RUNNING``、``ERROR``）。"""
        return self._state

    @property
    def messages(self) -> list[Message]:
        """当前对话消息的副本。可安全修改。"""
        return list(self._messages)

    @property
    def usage(self) -> Usage:
        """整个会话的累计 token 使用量。

        返回:
            :class:`Usage`，包含从会话开始以来所有 API 调用的
            输入/输出/缓存创建/缓存读取总量。
        """
        s = self._session
        return Usage(
            input_tokens=s.total_input_tokens,
            output_tokens=s.total_output_tokens,
            cache_read_input_tokens=s.total_cache_read_tokens,
            cache_creation_input_tokens=s.total_cache_write_tokens,
        )

    @property
    def last_usage(self) -> Usage:
        """最近完成的 API 调用报告的使用量。

        在新会话中、任何调用完成之前为零。
        """
        return self._last_usage

    @property
    def session_id(self) -> str:
        """十六进制编码的会话 ID。用作 ``.pivot/sessions/`` 中的键。"""
        return self._session.session_id

    @property
    def cost_usd(self) -> float:
        """累计估计的会话成本（美元）。

        参见 :attr:`cost_unknown`——当模型没有可用定价时
        值为 ``0.0``（通常用于本地模型或非常新的版本）。
        """
        return self._session.total_cost_usd

    @property
    def cost_unknown(self) -> bool:
        """``True`` 表示模型的定价不在注册表中。

        当为 ``True`` 时，:attr:`cost_usd` 不是有效的美元数字
        （通常用于本地模型或非常新的版本）。
        """
        return self._session.cost_unknown

    @property
    def cwd(self) -> str:
        """代理操作的工作目录。"""
        return self._cwd

    @property
    def turn_count(self) -> int:
        """此会话中处理的用户消息数。"""
        return self._session.turn_count

    # ── 异步内部方法 ───────────────────────────────────────────────────

    async def _query_text_async(self, message: str) -> str:
        """消费事件流并仅返回最终文本。"""
        last_text = ""
        async for event in self.query_events_async(message):
            if isinstance(event, AssistantMessage) and not event.hide_in_api:
                last_text = event.text
        return last_text

    async def _query_events_collect_async(self, message: str) -> list:
        """将事件流消费到列表中。"""
        events: list = []
        async for event in self.query_events_async(message):
            events.append(event)
        return events

    def update_session_setting(self, key: str, value: Any) -> str | None:
        """验证并更新此会话的内存和磁盘设置。

        所有设置都可以在会话期间更改。后端相关设置
        （``backend``、``model``、``api_key``、``base_url``）会触发
        新的 ``LLMProvider`` 实例。其他设置在下一轮生效。

        单独更新 ``model`` 也会重新推断 ``backend``（纯 Claude
        名称将后端切换到 ``anthropic-native``；其他切换到 ``auto``）。
        显式传递 ``backend`` 以覆盖推断。

        验证失败时返回错误消息字符串，成功时返回 None。
        """
        from pivotcode.settings import BACKEND_SETTINGS

        # 接受旧版 ``provider`` 键作为 ``backend`` 的别名，
        # 转换其旧值。/provider 斜杠命令和任何依赖旧名称的
        # 外部调用者可以继续工作。
        if key == "provider":
            key = "backend"
            if isinstance(value, str):
                from pivotcode.settings import _LEGACY_PROVIDER_MAP

                value = _LEGACY_PROVIDER_MAP.get(value.lower(), value)

        if key not in SETTINGS_DEFAULTS:
            return f"Unknown setting '{key}'."

        error = validate_setting(key, value)
        if error:
            return error

        self._settings[key] = value

        # 同步对应的 self._* 字段
        field_map = {
            "model": "_model",
            "permission_mode": "_permission_mode",
            "max_iterations_per_turn": "_max_iterations_per_turn",
            "max_output_tokens": "_max_output_tokens",
            "memory": "_memory_mode",
            "verbose": "_verbose",
        }
        attr = field_map.get(key)
        if attr:
            setattr(self, attr, value)

        # 仅在模型更改时重新推断后端。新后端可能与旧后端相同
        # （此时这是空操作），也可能切换——例如从 gpt-4o 切换到
        # claude-sonnet-4-6 会将 auto 提升为 anthropic-native。
        if key == "model":
            inferred = infer_backend(value)
            if inferred != self._settings.get("backend"):
                self._settings["backend"] = inferred

        # 如果后端相关设置更改则重新创建底层 LLMProvider
        if key in BACKEND_SETTINGS:
            try:
                self._provider = _create_provider_from_settings(self._settings)
                logger.info("Backend recreated: %s / %s",
                           self._settings.get("backend"), self._settings.get("model"))
            except Exception as e:
                return f"Failed to create backend: {e}"

        save_session_settings(self._cwd, self._session_id, self._settings)
        return None

    def update_project_setting(self, key: str, value: Any) -> str | None:
        """验证并更新项目的 .pivot/settings.json 中的设置。

        不会修改内存中的代理状态——仅修改磁盘上的项目默认值。

        验证失败时返回错误消息字符串，成功时返回 None。
        """
        # 将旧版 ``provider`` 键转换为 ``backend``。
        if key == "provider":
            key = "backend"
            if isinstance(value, str):
                from pivotcode.settings import _LEGACY_PROVIDER_MAP

                value = _LEGACY_PROVIDER_MAP.get(value.lower(), value)

        if key not in SETTINGS_DEFAULTS:
            return f"Unknown setting '{key}'."

        error = validate_setting(key, value)
        if error:
            return error

        settings = load_settings(self._cwd)
        settings[key] = value
        save_settings(settings, self._cwd)
        return None


# ── 异步辅助函数 ────────────────────────────────────────────────────────────


def _run_async(coro):
    """从同步代码运行异步协程。

    处理事件循环已在运行的情况（例如 Jupyter）。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # 在现有异步上下文中（Jupyter、嵌套异步）。
        # 创建新线程运行协程。
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()

    return asyncio.run(coro)


def _run_async_safe(coro):
    """类似 _run_async，但在失败时返回 None 而不是引发异常。"""
    try:
        return _run_async(coro)
    except Exception:
        logger.debug("Async operation failed (ignored)", exc_info=True)
        return None
