"""
AgentWatch remote client — send traces to a central AgentWatch server.

For agents running on machines separate from the AgentWatch dashboard.
Uses HTTP to POST traces, logs, health checks, and costs to the server's
ingestion API.

Usage:
    from agentwatch.client import AgentWatchClient

    client = AgentWatchClient(
        server_url="http://dashboard-host:8470",
        agent_name="my-remote-agent",
        auth_token="secret",  # optional
    )

    # Send a completed trace
    with client.trace("process-batch") as span:
        span.event("processing 50 items")
        do_work()
        span.set_metadata("items", 50)

    # Send a log
    client.log("info", "Agent started")

    # Send health check result
    client.health("database", status="ok", message="connected")

    # Send cost record
    client.cost(model="claude-sonnet-4-20250514", input_tokens=1000, output_tokens=200)

    # Flush any buffered data (called automatically on trace completion)
    client.flush()
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generator
from urllib.request import Request, urlopen
from urllib.error import URLError


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class ClientSpanEvent:
    """A span event for the remote client."""
    id: str = field(default_factory=_uuid)
    timestamp: str = field(default_factory=lambda: _now().isoformat())
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "message": self.message,
            "metadata": self.metadata,
        }


@dataclass
class ClientSpan:
    """A span being built for the remote client."""
    id: str = field(default_factory=_uuid)
    trace_id: str = ""
    parent_id: str | None = None
    name: str = ""
    status: str = "running"
    started_at: str = field(default_factory=lambda: _now().isoformat())
    ended_at: str | None = None
    duration_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    events: list[ClientSpanEvent] = field(default_factory=list)
    error: str | None = None
    _children: list[ClientSpan] = field(default_factory=list)

    def event(self, message: str, metadata: dict[str, Any] | None = None) -> None:
        """Add an event to this span."""
        self.events.append(ClientSpanEvent(
            message=message,
            metadata=metadata or {},
        ))

    def set_metadata(self, key: str, value: Any) -> None:
        """Set a metadata key on this span."""
        self.metadata[key] = value

    def set_error(self, error: str) -> None:
        """Mark this span as failed with an error message."""
        self.error = error
        self.status = "failed"

    def _finish(self, status: str = "completed") -> None:
        """Finish this span, computing duration."""
        now = _now()
        self.ended_at = now.isoformat()
        start = datetime.fromisoformat(self.started_at)
        self.duration_ms = (now - start).total_seconds() * 1000
        if self.status == "running":
            self.status = status

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
            "events": [e.to_dict() for e in self.events],
            "error": self.error,
        }


class ClientTrace:
    """
    Context manager for building and sending a trace.

    Usage:
        with client.trace("my-task") as span:
            span.event("started")
            do_work()

        # Trace is automatically sent to the server on exit
    """

    def __init__(
        self,
        client: AgentWatchClient,
        name: str,
        metadata: dict[str, Any] | None = None,
    ):
        self._client = client
        self._trace_id = _uuid()
        self._name = name
        self._metadata = metadata or {}
        self._root_span = ClientSpan(
            trace_id=self._trace_id,
            name=name,
        )
        self._span_stack: list[ClientSpan] = [self._root_span]
        self._all_spans: list[ClientSpan] = [self._root_span]

    @property
    def span(self) -> ClientSpan:
        """Current active span."""
        return self._span_stack[-1]

    def event(self, message: str, metadata: dict[str, Any] | None = None) -> None:
        """Add an event to the current span."""
        self.span.event(message, metadata)

    def set_metadata(self, key: str, value: Any) -> None:
        """Set metadata on the current span."""
        self.span.set_metadata(key, value)

    def set_error(self, error: str) -> None:
        """Mark the current span as failed."""
        self.span.set_error(error)

    @contextmanager
    def child(self, name: str) -> Generator[ClientSpan, None, None]:
        """Create a child span within the current trace."""
        parent = self._span_stack[-1]
        child_span = ClientSpan(
            trace_id=self._trace_id,
            parent_id=parent.id,
            name=name,
        )
        self._all_spans.append(child_span)
        self._span_stack.append(child_span)
        try:
            yield child_span
        except Exception as e:
            child_span.set_error(str(e))
            raise
        finally:
            child_span._finish()
            self._span_stack.pop()

    def __enter__(self) -> ClientTrace:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type:
            self._root_span.set_error(str(exc_val))
            self._root_span._finish("failed")
        else:
            self._root_span._finish("completed")

        trace_data = {
            "id": self._trace_id,
            "agent_name": self._client.agent_name,
            "name": self._name,
            "status": self._root_span.status,
            "started_at": self._root_span.started_at,
            "ended_at": self._root_span.ended_at,
            "duration_ms": self._root_span.duration_ms,
            "metadata": {**self._metadata, **self._root_span.metadata},
            "spans": [s.to_dict() for s in self._all_spans],
        }

        self._client._send("traces", trace_data)


class AgentWatchClient:
    """
    HTTP client for sending observability data to a central AgentWatch server.

    Thread-safe. Optionally buffers records and flushes in batches.

    Args:
        server_url: Base URL of the AgentWatch server (e.g., "http://host:8470").
        agent_name: Name of this agent (used as default for all records).
        auth_token: Optional auth token for the server.
        buffer_size: Number of records to buffer before auto-flushing (0 = immediate).
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        server_url: str,
        agent_name: str = "remote-agent",
        auth_token: str | None = None,
        buffer_size: int = 0,
        timeout: float = 10.0,
    ):
        self.server_url = server_url.rstrip("/")
        self.agent_name = agent_name
        self.auth_token = auth_token
        self.buffer_size = buffer_size
        self.timeout = timeout

        self._buffer: dict[str, list[dict[str, Any]]] = {
            "traces": [],
            "logs": [],
            "health": [],
            "costs": [],
        }
        self._lock = threading.Lock()
        self._total_sent = 0
        self._errors = 0

    def trace(
        self,
        name: str,
        metadata: dict[str, Any] | None = None,
    ) -> ClientTrace:
        """
        Create a trace context manager.

        Usage:
            with client.trace("my-task") as t:
                t.event("started processing")
                do_work()
                t.set_metadata("items", 50)

                with t.child("sub-task") as child:
                    child.event("doing sub-work")
        """
        return ClientTrace(self, name, metadata)

    def log(
        self,
        level: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> None:
        """Send a log entry."""
        self._send("logs", {
            "agent_name": self.agent_name,
            "level": level,
            "message": message,
            "timestamp": _now().isoformat(),
            "metadata": metadata or {},
            "trace_id": trace_id,
        })

    def health(
        self,
        name: str,
        status: str = "ok",
        message: str = "",
        duration_ms: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Send a health check result."""
        self._send("health", {
            "name": name,
            "agent_name": self.agent_name,
            "status": status,
            "message": message,
            "timestamp": _now().isoformat(),
            "duration_ms": duration_ms,
            "metadata": metadata or {},
        })

    def cost(
        self,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float | None = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Send a token usage / cost record."""
        self._send("costs", {
            "agent_name": self.agent_name,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": cost_usd,
            "timestamp": _now().isoformat(),
            "trace_id": trace_id,
            "metadata": metadata or {},
        })

    def metric(
        self,
        name: str,
        value: float,
        kind: str = "gauge",
        tags: dict[str, str] | None = None,
        trace_id: str | None = None,
    ) -> None:
        """Send a custom metric data point."""
        self._send("metrics", {
            "agent_name": self.agent_name,
            "name": name,
            "value": value,
            "kind": kind,
            "tags": tags or {},
            "timestamp": _now().isoformat(),
            "trace_id": trace_id,
        })

    def flush(self) -> int:
        """
        Flush any buffered records to the server.

        Returns:
            Number of records flushed.
        """
        with self._lock:
            batch = {k: list(v) for k, v in self._buffer.items()}
            for v in self._buffer.values():
                v.clear()

        total = sum(len(v) for v in batch.values())
        if total == 0:
            return 0

        # Remove empty categories
        batch = {k: v for k, v in batch.items() if v}

        try:
            self._http_post("/api/v1/ingest/batch", batch)
            self._total_sent += total
            return total
        except Exception:
            # Put items back in buffer on failure
            with self._lock:
                for k, v in batch.items():
                    self._buffer[k].extend(v)
            self._errors += 1
            raise

    @property
    def stats(self) -> dict[str, Any]:
        """Get client statistics."""
        with self._lock:
            buffered = sum(len(v) for v in self._buffer.values())
        return {
            "total_sent": self._total_sent,
            "buffered": buffered,
            "errors": self._errors,
        }

    def _send(self, category: str, data: dict[str, Any]) -> None:
        """Send or buffer a single record."""
        if self.buffer_size > 0:
            with self._lock:
                self._buffer[category].append(data)
                total_buffered = sum(len(v) for v in self._buffer.values())

            if total_buffered >= self.buffer_size:
                self.flush()
        else:
            # Send immediately
            try:
                self._http_post(f"/api/v1/ingest/{category}", data)
                self._total_sent += 1
            except Exception:
                self._errors += 1
                raise

    def _http_post(self, path: str, data: Any) -> dict[str, Any]:
        """Make an HTTP POST request to the server."""
        url = f"{self.server_url}{path}"
        body = json.dumps(data).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "agentwatch-client/0.1.0",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        req = Request(url, data=body, headers=headers, method="POST")

        try:
            with urlopen(req, timeout=self.timeout) as resp:
                result: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
                return result
        except URLError as e:
            raise ConnectionError(
                f"Failed to connect to AgentWatch server at {url}: {e}"
            ) from e

    def __repr__(self) -> str:
        return (
            f"AgentWatchClient(server={self.server_url!r}, "
            f"agent={self.agent_name!r}, "
            f"sent={self._total_sent})"
        )
