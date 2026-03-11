"""
HTTP ingestion API for AgentWatch.

Accepts traces, logs, health checks, and cost records from remote agents
over HTTP. This enables a central AgentWatch server to collect data from
agents running on different machines.

Remote agents use the lightweight `AgentWatchClient` to send data,
or POST directly to the API endpoints:

    POST /api/v1/ingest/traces    — submit a complete trace with spans
    POST /api/v1/ingest/logs      — submit log entries
    POST /api/v1/ingest/health    — submit health check results
    POST /api/v1/ingest/costs     — submit token usage records
    POST /api/v1/ingest/batch     — submit multiple record types at once

All endpoints accept JSON and return {"status": "ok", "ingested": N}.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from agentwatch.models import (
    HealthCheck,
    HealthStatus,
    LogEntry,
    LogLevel,
    Span,
    SpanEvent,
    Trace,
    TraceStatus,
)
from agentwatch.costs import TokenUsage
from agentwatch.storage import Storage


def _parse_ts(s: str | None) -> datetime:
    """Parse an ISO timestamp, defaulting to now."""
    if not s:
        return datetime.now(timezone.utc)
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _gen_id() -> str:
    return uuid.uuid4().hex[:16]


def ingest_trace(data: dict[str, Any], storage: Storage) -> str:
    """
    Ingest a single trace with optional spans and events.

    Args:
        data: Trace dict with keys: name, agent_name, status, started_at,
              ended_at, duration_ms, metadata, spans[].
        storage: Storage instance.

    Returns:
        The trace ID.
    """
    trace_id = data.get("id") or _gen_id()

    trace = Trace(
        id=trace_id,
        agent_name=data.get("agent_name", "remote"),
        name=data.get("name", "unnamed"),
        status=TraceStatus(data.get("status", "completed")),
        started_at=_parse_ts(data.get("started_at")),
        ended_at=_parse_ts(data.get("ended_at")) if data.get("ended_at") else None,
        duration_ms=data.get("duration_ms"),
        metadata=data.get("metadata", {}),
    )

    # Build root span if spans are provided
    spans_data = data.get("spans", [])
    if spans_data:
        # First span without parent_id is root, or we create a synthetic one
        root_data = None
        child_data = []
        for s in spans_data:
            if not s.get("parent_id"):
                if root_data is None:
                    root_data = s
                else:
                    child_data.append(s)
            else:
                child_data.append(s)

        if root_data:
            trace.root_span = _build_span(root_data, trace_id)
    elif not spans_data:
        # Create a root span from the trace itself
        trace.root_span = Span(
            id=_gen_id(),
            trace_id=trace_id,
            name=trace.name,
            status=trace.status,
            started_at=trace.started_at,
            ended_at=trace.ended_at,
            duration_ms=trace.duration_ms,
            metadata=trace.metadata,
        )

    storage.save_trace(trace)

    # Save additional child spans
    if spans_data:
        for s in spans_data:
            if s.get("parent_id") or (root_data and s is not root_data):
                if s is not root_data:
                    span = _build_span(s, trace_id)
                    storage.save_span(span)

    return trace_id


def ingest_log(data: dict[str, Any], storage: Storage) -> str:
    """
    Ingest a single log entry.

    Args:
        data: Log dict with keys: agent_name, level, message, timestamp,
              metadata, trace_id, span_id.
        storage: Storage instance.

    Returns:
        The log entry ID.
    """
    entry = LogEntry(
        id=data.get("id") or _gen_id(),
        agent_name=data.get("agent_name", "remote"),
        level=LogLevel(data.get("level", "info").lower()),
        message=data.get("message", ""),
        timestamp=_parse_ts(data.get("timestamp")),
        metadata=data.get("metadata", {}),
        trace_id=data.get("trace_id"),
        span_id=data.get("span_id"),
    )
    storage.save_log(entry)
    return entry.id


def ingest_health(data: dict[str, Any], storage: Storage) -> str:
    """
    Ingest a health check result.

    Args:
        data: Health dict with keys: name, agent_name, status, message,
              timestamp, duration_ms, metadata.
        storage: Storage instance.

    Returns:
        The check name (health checks don't have user-facing IDs).
    """
    check = HealthCheck(
        name=data.get("name", "unknown"),
        agent_name=data.get("agent_name", "remote"),
        status=HealthStatus(data.get("status", "unknown").lower()),
        message=data.get("message", ""),
        timestamp=_parse_ts(data.get("timestamp")),
        duration_ms=data.get("duration_ms"),
        metadata=data.get("metadata", {}),
    )
    storage.save_health_check(check)
    return check.name


def ingest_cost(data: dict[str, Any], storage: Storage) -> str:
    """
    Ingest a token usage / cost record.

    Args:
        data: Cost dict with keys: agent_name, model, input_tokens,
              output_tokens, estimated_cost_usd, timestamp, trace_id,
              span_id, metadata.
        storage: Storage instance.

    Returns:
        The usage record ID.
    """
    usage_id = data.get("id") or _gen_id()
    input_tokens = data.get("input_tokens", 0)
    output_tokens = data.get("output_tokens", 0)

    # Estimate cost if not provided
    cost = data.get("estimated_cost_usd")
    if cost is None:
        from agentwatch.costs import estimate_cost
        cost = estimate_cost(
            data.get("model", "unknown"),
            input_tokens,
            output_tokens,
        )

    usage = TokenUsage(
        id=usage_id,
        agent_name=data.get("agent_name", "remote"),
        model=data.get("model", "unknown"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        estimated_cost_usd=cost,
        timestamp=_parse_ts(data.get("timestamp")),
        trace_id=data.get("trace_id"),
        span_id=data.get("span_id"),
        metadata=data.get("metadata", {}),
    )
    storage.save_token_usage(usage)
    return usage_id


def ingest_metric(data: dict[str, Any], storage: Storage) -> str:
    """
    Ingest a custom metric data point.

    Args:
        data: Metric dict with keys: name, value, kind, tags, agent_name,
              timestamp, trace_id, span_id.
        storage: Storage instance.

    Returns:
        The metric point ID.
    """
    from agentwatch.metrics import MetricPoint

    point = MetricPoint(
        id=data.get("id") or _gen_id(),
        agent_name=data.get("agent_name", "remote"),
        name=data.get("name", "unnamed"),
        value=float(data.get("value", 0)),
        kind=data.get("kind", "gauge"),
        tags=data.get("tags", {}),
        timestamp=_parse_ts(data.get("timestamp")),
        trace_id=data.get("trace_id"),
        span_id=data.get("span_id"),
    )
    storage.save_metric(point)
    return point.id


def ingest_batch(
    data: dict[str, Any],
    storage: Storage,
) -> dict[str, int]:
    """
    Ingest a batch of mixed records.

    Args:
        data: Dict with optional keys: traces[], logs[], health[], costs[].
        storage: Storage instance.

    Returns:
        Dict of counts: {"traces": N, "logs": N, "health": N, "costs": N}.
    """
    counts = {"traces": 0, "logs": 0, "health": 0, "costs": 0, "metrics": 0, "model_usage": 0, "cron_runs": 0}

    for trace_data in data.get("traces", []):
        ingest_trace(trace_data, storage)
        counts["traces"] += 1

    for log_data in data.get("logs", []):
        ingest_log(log_data, storage)
        counts["logs"] += 1

    for health_data in data.get("health", []):
        ingest_health(health_data, storage)
        counts["health"] += 1

    for cost_data in data.get("costs", []):
        ingest_cost(cost_data, storage)
        counts["costs"] += 1

    for metric_data in data.get("metrics", []):
        ingest_metric(metric_data, storage)
        counts["metrics"] += 1

    for mu_data in data.get("model_usage", []):
        ingest_model_usage(mu_data, storage)
        counts["model_usage"] += 1

    for cr_data in data.get("cron_runs", []):
        ingest_cron_run(cr_data, storage)
        counts["cron_runs"] += 1

    return counts


def ingest_model_usage(data: dict[str, Any], storage: Storage) -> str:
    """
    Ingest a single model usage record.

    Args:
        data: Dict with keys: model, prompt_tokens, completion_tokens,
              cost_usd, latency_ms (optional), agent_name (optional).
        storage: Storage instance.

    Returns:
        The record ID.
    """
    return storage.record_model_usage(
        model=data.get("model", "unknown"),
        prompt_tokens=int(data.get("prompt_tokens", 0)),
        completion_tokens=int(data.get("completion_tokens", 0)),
        cost_usd=float(data.get("cost_usd", 0.0)),
        latency_ms=float(data["latency_ms"]) if data.get("latency_ms") is not None else None,
        agent_name=data.get("agent_name", "unknown"),
    )


def ingest_cron_run(data: dict[str, Any], storage: Storage) -> str:
    """
    Ingest a cron job run result.

    Args:
        data: Dict with keys: job_name, status, duration_ms (optional),
              error (optional), agent_name (optional).
        storage: Storage instance.

    Returns:
        The record ID.
    """
    return storage.record_cron_run(
        job_name=data.get("job_name", "unknown"),
        status=data.get("status", "unknown"),
        duration_ms=float(data["duration_ms"]) if data.get("duration_ms") is not None else None,
        error=data.get("error"),
        agent_name=data.get("agent_name"),
    )


def _build_span(data: dict[str, Any], trace_id: str) -> Span:
    """Build a Span from a dict."""
    span = Span(
        id=data.get("id") or _gen_id(),
        trace_id=trace_id,
        parent_id=data.get("parent_id"),
        name=data.get("name", "unnamed"),
        status=TraceStatus(data.get("status", "completed")),
        started_at=_parse_ts(data.get("started_at")),
        ended_at=_parse_ts(data.get("ended_at")) if data.get("ended_at") else None,
        duration_ms=data.get("duration_ms"),
        metadata=data.get("metadata", {}),
        error=data.get("error"),
    )

    for evt_data in data.get("events", []):
        evt = SpanEvent(
            id=evt_data.get("id") or _gen_id(),
            span_id=span.id,
            timestamp=_parse_ts(evt_data.get("timestamp")),
            message=evt_data.get("message", ""),
            metadata=evt_data.get("metadata", {}),
        )
        span.events.append(evt)

    return span
