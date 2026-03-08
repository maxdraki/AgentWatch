"""
Cost tracking for AgentWatch.

Track token usage and estimate costs across LLM providers.
Designed for agents that make many API calls — gives you
visibility into what your agent is actually spending.

Usage:
    import agentwatch

    agentwatch.init("my-agent")

    # Record token usage manually
    agentwatch.costs.record(
        model="claude-sonnet-4-20250514",
        input_tokens=1500,
        output_tokens=300,
    )

    # Or within a trace for automatic linking
    with agentwatch.trace("summarise") as span:
        result = call_llm(prompt)
        agentwatch.costs.record(
            model="claude-sonnet-4-20250514",
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
        )

    # Get cost summary
    summary = agentwatch.costs.summary()
    # {"total_cost_usd": 0.42, "total_tokens": 15000, ...}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agentwatch.models import _now, _uuid


# ─── Pricing data (USD per 1M tokens) ────────────────────────────────────────
# Last updated: 2026-03. Prices change — users can override with custom pricing.

PROVIDER_PRICING: dict[str, dict[str, tuple[float, float]]] = {
    # model_name: (input_per_1M, output_per_1M)
    # Anthropic (2025-2026)
    "claude-opus-4-20250514": (15.0, 75.0),
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-haiku-3-20250401": (0.25, 1.25),
    "claude-3.5-sonnet-20241022": (3.0, 15.0),
    "claude-3.5-haiku-20241022": (0.80, 4.0),
    # OpenAI (2025-2026)
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.0, 30.0),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "o1": (15.0, 60.0),
    "o1-mini": (1.10, 4.40),
    "o3": (10.0, 40.0),
    "o3-mini": (1.10, 4.40),
    "o4-mini": (1.10, 4.40),
    # Google (2025-2026)
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.0-flash-lite": (0.075, 0.30),
    # Meta (via providers)
    "llama-4-maverick": (0.50, 0.70),
    "llama-4-scout": (0.20, 0.40),
    "llama-3.3-70b": (0.60, 0.60),
    "llama-3.1-405b": (3.0, 3.0),
    "llama-3.1-70b": (0.90, 0.90),
    "llama-3.1-8b": (0.10, 0.10),
    # DeepSeek
    "deepseek-v3": (0.27, 1.10),
    "deepseek-r1": (0.55, 2.19),
    # Mistral
    "mistral-large": (2.0, 6.0),
    "mistral-small": (0.10, 0.30),
}

# Aliases for common variations
MODEL_ALIASES: dict[str, str] = {
    "claude-3-opus": "claude-opus-4-20250514",
    "claude-3-sonnet": "claude-sonnet-4-20250514",
    "claude-3-haiku": "claude-haiku-3-20250401",
    "claude-3.5-sonnet": "claude-3.5-sonnet-20241022",
    "claude-4-sonnet": "claude-sonnet-4-20250514",
    "claude-4-opus": "claude-opus-4-20250514",
    "sonnet": "claude-sonnet-4-20250514",
    "opus": "claude-opus-4-20250514",
    "haiku": "claude-haiku-3-20250401",
    "gpt4o": "gpt-4o",
    "gpt4": "gpt-4-turbo",
    "gemini-pro": "gemini-2.5-pro",
    "gemini-flash": "gemini-2.5-flash",
}


@dataclass
class TokenUsage:
    """A single token usage record."""

    id: str = field(default_factory=_uuid)
    agent_name: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    timestamp: datetime = field(default_factory=_now)
    trace_id: str | None = None
    span_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_name": self.agent_name,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "timestamp": self.timestamp.isoformat(),
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "metadata": self.metadata,
        }


@dataclass
class CostSummary:
    """Aggregated cost summary."""

    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    record_count: int = 0
    by_model: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_agent: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cost_usd": round(self.total_cost_usd, 4),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "record_count": self.record_count,
            "by_model": self.by_model,
            "by_agent": self.by_agent,
        }


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Estimate cost in USD for a given model and token count.

    Looks up pricing from the built-in table. Handles aliases
    and partial matches. Returns 0.0 for unknown models.

    Args:
        model: Model name (e.g., "claude-sonnet-4-20250514", "gpt-4o").
        input_tokens: Number of input/prompt tokens.
        output_tokens: Number of output/completion tokens.

    Returns:
        Estimated cost in USD.
    """
    resolved = _resolve_model(model)
    pricing = PROVIDER_PRICING.get(resolved)

    if not pricing:
        return 0.0

    input_cost = (input_tokens / 1_000_000) * pricing[0]
    output_cost = (output_tokens / 1_000_000) * pricing[1]

    return input_cost + output_cost


def record(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> TokenUsage:
    """
    Record token usage for cost tracking.

    Automatically estimates cost if not provided. Links to the
    current trace context if called within a trace block.

    Args:
        model: Model name.
        input_tokens: Input/prompt token count.
        output_tokens: Output/completion token count.
        cost_usd: Exact cost if known (skips estimation).
        trace_id: Explicit trace ID (auto-detected if in trace context).
        span_id: Explicit span ID (auto-detected if in trace context).
        metadata: Additional metadata.

    Returns:
        The TokenUsage record.
    """
    from agentwatch.core import get_agent
    from agentwatch.tracing import _get_current_span

    # Auto-link to current trace context
    current_span = _get_current_span()
    if current_span and not trace_id:
        trace_id = current_span.trace_id
        span_id = span_id or current_span.id

    # Estimate cost
    if cost_usd is not None:
        estimated = cost_usd
    else:
        estimated = estimate_cost(model, input_tokens, output_tokens)

    try:
        agent = get_agent()
        agent_name = agent.name
    except RuntimeError:
        agent_name = "unknown"

    usage = TokenUsage(
        agent_name=agent_name,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        estimated_cost_usd=estimated,
        trace_id=trace_id,
        span_id=span_id,
        metadata=metadata or {},
    )

    # Persist
    try:
        agent = get_agent()
        agent.storage.save_token_usage(usage)
    except (RuntimeError, AttributeError):
        pass  # Storage not available or method not yet added

    return usage


def summary(
    agent_name: str | None = None,
    hours: int | None = None,
) -> CostSummary:
    """
    Get a cost summary with breakdowns by model and agent.

    Args:
        agent_name: Filter to a specific agent.
        hours: Limit to the last N hours. None = all time.

    Returns:
        CostSummary with totals and breakdowns.
    """
    from agentwatch.core import get_agent

    try:
        agent = get_agent()
        storage = agent.storage
        records = storage.get_token_usage(agent_name=agent_name, hours=hours)
    except (RuntimeError, AttributeError, Exception):
        records = []

    result = CostSummary()

    by_model: dict[str, dict[str, Any]] = {}
    by_agent: dict[str, dict[str, Any]] = {}

    for r in records:
        cost = r.get("estimated_cost_usd", 0)
        inp = r.get("input_tokens", 0)
        out = r.get("output_tokens", 0)
        total = r.get("total_tokens", 0)

        result.total_cost_usd += cost
        result.total_input_tokens += inp
        result.total_output_tokens += out
        result.total_tokens += total
        result.record_count += 1

        # By model
        model = r.get("model", "unknown")
        if model not in by_model:
            by_model[model] = {"cost_usd": 0, "input_tokens": 0, "output_tokens": 0, "count": 0}
        by_model[model]["cost_usd"] += cost
        by_model[model]["input_tokens"] += inp
        by_model[model]["output_tokens"] += out
        by_model[model]["count"] += 1

        # By agent
        agent = r.get("agent_name", "unknown")
        if agent not in by_agent:
            by_agent[agent] = {"cost_usd": 0, "tokens": 0, "count": 0}
        by_agent[agent]["cost_usd"] += cost
        by_agent[agent]["tokens"] += total
        by_agent[agent]["count"] += 1

    # Round costs in breakdowns
    for v in by_model.values():
        v["cost_usd"] = round(v["cost_usd"], 4)
    for v in by_agent.values():
        v["cost_usd"] = round(v["cost_usd"], 4)

    result.by_model = by_model
    result.by_agent = by_agent

    return result


def set_pricing(model: str, input_per_1m: float, output_per_1m: float) -> None:
    """
    Override or add pricing for a model.

    Args:
        model: Model name.
        input_per_1m: Cost per 1M input tokens (USD).
        output_per_1m: Cost per 1M output tokens (USD).
    """
    PROVIDER_PRICING[model] = (input_per_1m, output_per_1m)


# ─── Internal ─────────────────────────────────────────────────────────────────

def _resolve_model(model: str) -> str:
    """Resolve model aliases and do fuzzy matching."""
    # Exact match
    if model in PROVIDER_PRICING:
        return model

    # Check aliases
    lower = model.lower().strip()
    if lower in MODEL_ALIASES:
        return MODEL_ALIASES[lower]

    # Partial match — find the best match by substring
    for known in PROVIDER_PRICING:
        if known in lower or lower in known:
            return known

    return model
