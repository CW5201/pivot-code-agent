"""Pivot Code — an open-source, provider-agnostic coding agent.

Simple usage::

    from pivotcode import PivotCodeAgent

    agent = PivotCodeAgent(model="openrouter/google/gemini-2.5-flash")
    answer = agent.query("What files are in this project?")
    print(answer)

The transport backend is inferred from the model string. A bare Claude
name (``claude-sonnet-4-6``) uses the native Anthropic SDK; any other
model goes through LiteLLM. Pass ``backend="anthropic-native" | "auto" |
"scripted"`` to override, or an ``LLMProvider`` instance for custom
transports.

Streaming::

    for event in agent.query_events("Fix the bug"):
        print(event)

Async::

    async for event in agent.query_events_async("Fix the bug"):
        ...
"""

from pivotcode.__version__ import __version__
from pivotcode.agent import PivotCodeAgent

__all__ = ["PivotCodeAgent", "__version__"]
