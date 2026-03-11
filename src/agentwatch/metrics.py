"""
Custom metrics for AgentWatch.

Track user-defined numeric values over time — gauges, counters, and
histograms. Useful for agent-specific metrics like queue depth, cache
hit rates, batch sizes, or any numeric KPI.

Usage::

    import agentwatch

    agentwatch.init("my-agent")

    # Record a gauge (point-in-time value)
    agentwatch.metric("queue_depth", 42)

    # Record with tags for filtering
    agentwatch.metric("requests", 1, tags={"method": "POST", "status": "200"})

    # Record with explicit type
    agentwatch.metric("cache_hits", 15, kind="counter")

    # Query metrics
    from agentwatch.metrics import query, summary
    points = query("queue_depth", hours=24)
    stats = summary("queue_depth", hours=1)

Metric kinds:
    - **gauge** (default): A point-in-time value (e.g. queue depth, memory usage).
    - **counter**: A monotonically increasing value (e.g. request count, errors).
      Counters can be reset to 0 but should never decrease otherwise.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from agentwatch.models import _now, _uuid


@dataclass
class MetricPoint:
    """A single metric data point."""

    id: str = field(default_factory=_uuid)
    agent_name: str = ""
    name: str = ""
    value: float = 0.0
    kind: str = "gauge"  # "gauge" or "counter"
    tags: dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_now)
    trace_id: str | None = None
    span_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_name": self.agent_name,
            "name": self.name,
            "value": self.value,
            "kind": self.kind,
            "tags": self.tags,
            "timestamp": self.timestamp.isoformat(),
            "trace_id": self.trace_id,
            "span_id": self.span_id,
        }


def record(
    name: str,
    value: float,
    kind: str = "gauge",
    tags: dict[str, str] | None = None,
    agent_name: str | None = None,
) -> MetricPoint:
    """
    Record a metric data point.

    Args:
        name: Metric name (e.g. "queue_depth", "cache_hit_rate").
        value: Numeric value.
        kind: "gauge" (point-in-time) or "counter" (monotonically increasing).
        tags: Optional key-value tags for filtering/grouping.
        agent_name: Override agent name (uses current agent if not set).

    Returns:
        The recorded MetricPoint.
    """
    from agentwatch.core import get_agent
    from agentwatch.tracing import _get_current_span

    agent = get_agent()
    resolved_name = agent_name or agent.name

    # Get trace context if available
    current_span = _get_current_span()
    trace_id = current_span.trace_id if current_span else None
    span_id = current_span.id if current_span else None

    point = MetricPoint(
        agent_name=resolved_name,
        name=name,
        value=float(value),
        kind=kind,
        tags=tags or {},
        trace_id=trace_id,
        span_id=span_id,
    )

    agent.storage.save_metric(point)
    return point


def query(
    name: str | None = None,
    agent_name: str | None = None,
    tags: dict[str, str] | None = None,
    hours: int | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """
    Query metric data points.

    Args:
        name: Filter by metric name.
        agent_name: Filter by agent.
        tags: Filter by tags (all specified tags must match).
        hours: Only include points from the last N hours.
        limit: Maximum number of results.

    Returns:
        List of metric point dicts, newest first.
    """
    from agentwatch.core import get_agent
    agent = get_agent()
    return agent.storage.get_metrics(
        name=name,
        agent_name=agent_name,
        tags=tags,
        hours=hours,
        limit=limit,
    )


def summary(
    name: str,
    agent_name: str | None = None,
    tags: dict[str, str] | None = None,
    hours: int | None = None,
) -> dict[str, Any]:
    """
    Get aggregate statistics for a metric.

    Returns:
        Dict with count, min, max, avg, sum, latest, and series data.
    """
    from agentwatch.core import get_agent
    agent = get_agent()
    return agent.storage.get_metric_summary(
        name=name,
        agent_name=agent_name,
        tags=tags,
        hours=hours,
    )


def list_metrics(
    agent_name: str | None = None,
) -> list[dict[str, Any]]:
    """
    List all known metric names with their latest values and types.

    Returns:
        List of dicts with name, kind, latest_value, count, agent_name.
    """
    from agentwatch.core import get_agent
    agent = get_agent()
    return agent.storage.list_metrics(agent_name=agent_name)
