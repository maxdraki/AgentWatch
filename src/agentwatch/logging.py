"""
Structured logging for AgentWatch.

Logs are stored in SQLite alongside traces and health checks,
giving you a unified view of what your agent is doing.

Usage:
    agentwatch.log("info", "Processing started", {"batch_size": 42})
    agentwatch.log("error", "API call failed", {"status": 500, "url": url})
"""

from __future__ import annotations

from typing import Any

from agentwatch.models import LogEntry, LogLevel
from agentwatch.tracing import _get_current_span


# Map string levels to enum
_LEVEL_MAP = {
    "debug": LogLevel.DEBUG,
    "info": LogLevel.INFO,
    "warn": LogLevel.WARN,
    "warning": LogLevel.WARN,
    "error": LogLevel.ERROR,
    "critical": LogLevel.CRITICAL,
}


def log(
    level: str | LogLevel,
    message: str,
    metadata: dict[str, Any] | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
) -> LogEntry:
    """
    Log a structured message.

    If called within a trace context, the log is automatically linked
    to the current span and trace.

    Args:
        level: Log level — "debug", "info", "warn", "error", or "critical".
        message: Human-readable log message.
        metadata: Optional dict of structured data.
        trace_id: Explicit trace ID (auto-detected if in a trace context).
        span_id: Explicit span ID (auto-detected if in a trace context).

    Returns:
        The created LogEntry.
    """
    from agentwatch.core import get_agent

    # Resolve level
    if isinstance(level, str):
        resolved_level = _LEVEL_MAP.get(level.lower())
        if not resolved_level:
            raise ValueError(
                f"Unknown log level '{level}'. Use: {', '.join(_LEVEL_MAP.keys())}"
            )
    else:
        resolved_level = level

    # Auto-link to current trace context
    current_span = _get_current_span()
    if current_span and not trace_id:
        trace_id = current_span.trace_id
        span_id = span_id or current_span.id

    try:
        agent = get_agent()
        agent_name = agent.name
    except RuntimeError:
        agent_name = "unknown"

    entry = LogEntry(
        agent_name=agent_name,
        level=resolved_level,
        message=message,
        metadata=metadata or {},
        trace_id=trace_id,
        span_id=span_id,
    )

    # Persist
    try:
        agent = get_agent()
        agent.storage.save_log(entry)
    except RuntimeError:
        pass  # Not initialised — log is still returned

    return entry
