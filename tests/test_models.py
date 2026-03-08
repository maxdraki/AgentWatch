"""Tests for AgentWatch data models."""

import time
from agentwatch.models import (
    Span,
    SpanEvent,
    Trace,
    LogEntry,
    HealthCheck,
    TraceStatus,
    LogLevel,
    HealthStatus,
)


def test_span_creation():
    span = Span(name="test-span", trace_id="trace-1")
    assert span.name == "test-span"
    assert span.status == TraceStatus.RUNNING
    assert span.ended_at is None
    assert span.duration_ms is None


def test_span_finish():
    span = Span(name="test-span", trace_id="trace-1")
    time.sleep(0.01)
    span.finish()
    assert span.status == TraceStatus.COMPLETED
    assert span.ended_at is not None
    assert span.duration_ms is not None
    assert span.duration_ms >= 10  # at least 10ms


def test_span_finish_with_error():
    span = Span(name="test-span", trace_id="trace-1")
    span.finish(status=TraceStatus.FAILED, error="something broke")
    assert span.status == TraceStatus.FAILED
    assert span.error == "something broke"


def test_span_events():
    span = Span(name="test-span", trace_id="trace-1")
    evt = span.event("found 3 items", {"count": 3})
    assert len(span.events) == 1
    assert evt.message == "found 3 items"
    assert evt.metadata == {"count": 3}
    assert evt.span_id == span.id


def test_span_to_dict():
    span = Span(name="test-span", trace_id="trace-1")
    span.event("hello")
    span.finish()
    d = span.to_dict()
    assert d["name"] == "test-span"
    assert d["status"] == "completed"
    assert len(d["events"]) == 1
    assert d["events"][0]["message"] == "hello"


def test_trace_creation():
    trace = Trace(agent_name="test-agent", name="test-trace")
    assert trace.status == TraceStatus.RUNNING
    assert trace.agent_name == "test-agent"


def test_trace_finish():
    trace = Trace(agent_name="test-agent", name="test-trace")
    trace.finish()
    assert trace.status == TraceStatus.COMPLETED
    assert trace.ended_at is not None


def test_trace_finish_inherits_failed_root():
    trace = Trace(agent_name="test-agent", name="test-trace")
    root = Span(name="root", trace_id=trace.id)
    root.finish(status=TraceStatus.FAILED, error="boom")
    trace.root_span = root
    trace.finish()
    assert trace.status == TraceStatus.FAILED


def test_log_entry():
    entry = LogEntry(
        agent_name="test",
        level=LogLevel.ERROR,
        message="something failed",
        metadata={"code": 500},
    )
    d = entry.to_dict()
    assert d["level"] == "error"
    assert d["message"] == "something failed"
    assert d["metadata"]["code"] == 500


def test_health_check():
    check = HealthCheck(
        name="db",
        agent_name="test",
        status=HealthStatus.OK,
        message="connected",
        duration_ms=5.2,
    )
    d = check.to_dict()
    assert d["status"] == "ok"
    assert d["duration_ms"] == 5.2
