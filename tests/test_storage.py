"""Tests for AgentWatch SQLite storage."""

import os
import tempfile

import pytest

from agentwatch.models import (
    HealthCheck,
    HealthStatus,
    LogEntry,
    LogLevel,
    Span,
    Trace,
    TraceStatus,
)
from agentwatch.storage import Storage


@pytest.fixture
def storage():
    """Create a temporary storage instance."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        s = Storage(db_path=db_path)
        yield s
        s.close()


class TestTraceStorage:
    def test_save_and_get_trace(self, storage: Storage):
        trace = Trace(agent_name="test-agent", name="my-trace")
        root = Span(name="root", trace_id=trace.id)
        root.event("step 1 done")
        root.finish()
        trace.root_span = root
        trace.finish()

        storage.save_trace(trace)

        # Get by ID
        result = storage.get_trace(trace.id)
        assert result is not None
        assert result["name"] == "my-trace"
        assert result["status"] == "completed"
        assert len(result["spans"]) == 1
        assert len(result["spans"][0]["events"]) == 1

    def test_list_traces(self, storage: Storage):
        for i in range(5):
            trace = Trace(agent_name="test-agent", name=f"trace-{i}")
            trace.finish()
            storage.save_trace(trace)

        results = storage.get_traces()
        assert len(results) == 5

    def test_filter_by_agent(self, storage: Storage):
        for name in ["agent-a", "agent-b", "agent-a"]:
            trace = Trace(agent_name=name, name="test")
            trace.finish()
            storage.save_trace(trace)

        results = storage.get_traces(agent_name="agent-a")
        assert len(results) == 2

    def test_filter_by_status(self, storage: Storage):
        t1 = Trace(agent_name="test", name="ok")
        t1.finish(status=TraceStatus.COMPLETED)
        storage.save_trace(t1)

        t2 = Trace(agent_name="test", name="fail")
        t2.finish(status=TraceStatus.FAILED)
        storage.save_trace(t2)

        results = storage.get_traces(status=TraceStatus.FAILED)
        assert len(results) == 1
        assert results[0]["name"] == "fail"

    def test_search_by_name(self, storage: Storage):
        """Should filter traces by name substring match."""
        for name in ["process-emails", "sync-calendar", "process-invoices"]:
            trace = Trace(agent_name="test", name=name)
            trace.finish()
            storage.save_trace(trace)

        results = storage.get_traces(name_contains="process")
        assert len(results) == 2
        names = {r["name"] for r in results}
        assert names == {"process-emails", "process-invoices"}

    def test_filter_by_duration(self, storage: Storage):
        """Should filter by duration range."""
        for i, dur in enumerate([100, 500, 2000, 5000]):
            trace = Trace(agent_name="test", name=f"trace-{i}")
            trace.duration_ms = dur
            trace.status = TraceStatus.COMPLETED
            storage.save_trace(trace)

        results = storage.get_traces(min_duration_ms=1000)
        assert len(results) == 2

        results = storage.get_traces(max_duration_ms=500)
        assert len(results) == 2

        results = storage.get_traces(min_duration_ms=200, max_duration_ms=3000)
        assert len(results) == 2

    def test_filter_by_hours(self, storage: Storage):
        """Should filter by time window."""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)

        # Recent
        t1 = Trace(agent_name="test", name="recent", started_at=now - timedelta(hours=1))
        t1.finish()
        storage.save_trace(t1)

        # Old
        t2 = Trace(agent_name="test", name="old", started_at=now - timedelta(hours=48))
        t2.finish()
        storage.save_trace(t2)

        results = storage.get_traces(hours=24)
        assert len(results) == 1
        assert results[0]["name"] == "recent"

    def test_nonexistent_trace(self, storage: Storage):
        assert storage.get_trace("nonexistent") is None


class TestLogStorage:
    def test_save_and_get_log(self, storage: Storage):
        entry = LogEntry(
            agent_name="test",
            level=LogLevel.ERROR,
            message="something broke",
            metadata={"code": 500},
        )
        storage.save_log(entry)

        logs = storage.get_logs()
        assert len(logs) == 1
        assert logs[0]["message"] == "something broke"
        assert logs[0]["metadata"]["code"] == 500

    def test_filter_by_level(self, storage: Storage):
        for level in [LogLevel.INFO, LogLevel.ERROR, LogLevel.INFO]:
            entry = LogEntry(agent_name="test", level=level, message=f"{level.value} msg")
            storage.save_log(entry)

        errors = storage.get_logs(level=LogLevel.ERROR)
        assert len(errors) == 1


class TestHealthStorage:
    def test_save_and_get_health(self, storage: Storage):
        check = HealthCheck(
            name="database",
            agent_name="test",
            status=HealthStatus.OK,
            message="connected",
            duration_ms=5.0,
        )
        storage.save_health_check(check)

        latest = storage.get_health_latest()
        assert len(latest) == 1
        assert latest[0]["name"] == "database"
        assert latest[0]["status"] == "ok"

    def test_latest_returns_most_recent(self, storage: Storage):
        # Save two checks for the same name
        c1 = HealthCheck(name="api", agent_name="test", status=HealthStatus.OK, message="ok")
        storage.save_health_check(c1)

        c2 = HealthCheck(name="api", agent_name="test", status=HealthStatus.CRITICAL, message="down")
        storage.save_health_check(c2)

        latest = storage.get_health_latest()
        assert len(latest) == 1
        assert latest[0]["status"] == "critical"

    def test_health_history(self, storage: Storage):
        for i in range(5):
            c = HealthCheck(name="db", agent_name="test", status=HealthStatus.OK, message=f"check-{i}")
            storage.save_health_check(c)

        history = storage.get_health_history("db")
        assert len(history) == 5


class TestStats:
    def test_basic_stats(self, storage: Storage):
        t = Trace(agent_name="my-agent", name="test")
        t.finish()
        storage.save_trace(t)

        entry = LogEntry(agent_name="my-agent", level=LogLevel.INFO, message="hi")
        storage.save_log(entry)

        stats = storage.get_stats()
        assert stats["total_traces"] == 1
        assert stats["total_logs"] == 1
        assert "my-agent" in stats["agents"]

    def test_stats_by_agent(self, storage: Storage):
        for name in ["a", "b", "a"]:
            t = Trace(agent_name=name, name="test")
            t.finish()
            storage.save_trace(t)

        stats = storage.get_stats(agent_name="a")
        assert stats["total_traces"] == 2
