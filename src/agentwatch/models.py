"""
Core data models for AgentWatch.

All models are plain dataclasses — no ORM, no magic. They map directly
to SQLite tables and JSON serialisation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return uuid.uuid4().hex[:16]


class TraceStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"


class HealthStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class Span:
    """A single unit of work within a trace."""

    id: str = field(default_factory=_uuid)
    trace_id: str = ""
    parent_id: str | None = None
    name: str = ""
    status: TraceStatus = TraceStatus.RUNNING
    started_at: datetime = field(default_factory=_now)
    ended_at: datetime | None = None
    duration_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    events: list[SpanEvent] = field(default_factory=list)
    error: str | None = None

    def finish(self, status: TraceStatus = TraceStatus.COMPLETED, error: str | None = None) -> None:
        self.ended_at = _now()
        self.duration_ms = (self.ended_at - self.started_at).total_seconds() * 1000
        self.status = status
        if error:
            self.error = error

    def event(self, message: str, metadata: dict[str, Any] | None = None) -> SpanEvent:
        """Add an event to this span."""
        evt = SpanEvent(
            span_id=self.id,
            message=message,
            metadata=metadata or {},
        )
        self.events.append(evt)
        return evt

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["started_at"] = self.started_at.isoformat()
        d["ended_at"] = self.ended_at.isoformat() if self.ended_at else None
        d["status"] = self.status.value
        d["events"] = [e.to_dict() for e in self.events]
        return d


@dataclass
class SpanEvent:
    """A discrete event within a span (like a log entry scoped to a trace)."""

    id: str = field(default_factory=_uuid)
    span_id: str = ""
    timestamp: datetime = field(default_factory=_now)
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "span_id": self.span_id,
            "timestamp": self.timestamp.isoformat(),
            "message": self.message,
            "metadata": self.metadata,
        }


@dataclass
class Trace:
    """A complete trace — a tree of spans representing one agent workflow."""

    id: str = field(default_factory=_uuid)
    agent_name: str = ""
    name: str = ""
    status: TraceStatus = TraceStatus.RUNNING
    started_at: datetime = field(default_factory=_now)
    ended_at: datetime | None = None
    duration_ms: float | None = None
    root_span: Span | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def finish(self, status: TraceStatus | None = None, error: str | None = None) -> None:
        self.ended_at = _now()
        self.duration_ms = (self.ended_at - self.started_at).total_seconds() * 1000
        if status:
            self.status = status
        elif self.root_span and self.root_span.status == TraceStatus.FAILED:
            self.status = TraceStatus.FAILED
        else:
            self.status = TraceStatus.COMPLETED

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id,
            "agent_name": self.agent_name,
            "name": self.name,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }
        if self.root_span:
            d["root_span"] = self.root_span.to_dict()
        return d


@dataclass
class LogEntry:
    """A structured log entry."""

    id: str = field(default_factory=_uuid)
    agent_name: str = ""
    level: LogLevel = LogLevel.INFO
    message: str = ""
    timestamp: datetime = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    span_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_name": self.agent_name,
            "level": self.level.value,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
        }


@dataclass
class HealthCheck:
    """Result of a single health check."""

    name: str = ""
    agent_name: str = ""
    status: HealthStatus = HealthStatus.UNKNOWN
    message: str = ""
    timestamp: datetime = field(default_factory=_now)
    duration_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "agent_name": self.agent_name,
            "status": self.status.value,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }
