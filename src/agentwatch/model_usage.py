"""
Model usage recording for AgentWatch.

A thin wrapper around storage.record_model_usage() that works with
the current agent context — similar to agentwatch.log() and
agentwatch.health().

Usage::

    import agentwatch

    agentwatch.record_model_usage(
        model="claude-sonnet-4-20250514",
        prompt_tokens=500,
        completion_tokens=200,
        cost_usd=0.0025,
        latency_ms=1234,
    )
"""

from __future__ import annotations

import agentwatch.core as _core


def record_model_usage(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    latency_ms: float | None = None,
    agent_name: str | None = None,
) -> str | None:
    """
    Record a model invocation with token counts, cost, and latency.

    Args:
        model: The model identifier (e.g. "claude-sonnet-4-20250514").
        prompt_tokens: Number of input/prompt tokens.
        completion_tokens: Number of output/completion tokens.
        cost_usd: Actual or estimated cost in USD.
        latency_ms: Request latency in milliseconds (optional).
        agent_name: Override the agent name. Defaults to the current agent.

    Returns:
        The record ID, or None if AgentWatch is not initialised.
    """
    agent = _core._agent
    if agent is None:
        return None

    resolved_agent = agent_name or agent.name
    return agent.storage.record_model_usage(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        agent_name=resolved_agent,
    )
