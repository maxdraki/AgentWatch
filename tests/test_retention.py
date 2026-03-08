"""Tests for data retention and database management."""

import json
import pytest
from datetime import datetime, timezone, timedelta
from io import StringIO

from agentwatch.storage import Storage
from agentwatch.models import (
    Trace, Span, SpanEvent, LogEntry, HealthCheck,
    TraceStatus, LogLevel, HealthStatus,
)
from agentwatch.costs import TokenUsage
from agentwatch.retention import prune, vacuum, db_info, export_jsonl, PruneResult


@pytest.fixture
def storage(tmp_path):
    """Create a temporary storage instance."""
    db_path = str(tmp_path / "test.db")
    return Storage(db_path=db_path)


@pytest.fixture
def aged_storage(storage):
    """Storage with data of varying ages."""
    now = datetime.now(timezone.utc)

    # Recent traces (5 days old)
    for i in range(3):
        trace = Trace(
            id=f"recent-{i}",
            agent_name="agent-a",
            name=f"recent-task-{i}",
            status=TraceStatus.COMPLETED,
            started_at=now - timedelta(days=5, hours=i),
            ended_at=now - timedelta(days=5, hours=i) + timedelta(seconds=1),
            duration_ms=1000,
        )
        span = Span(
            id=f"span-recent-{i}",
            trace_id=trace.id,
            name="root",
            status=TraceStatus.COMPLETED,
            started_at=trace.started_at,
            ended_at=trace.ended_at,
            duration_ms=1000,
        )
        span.events.append(SpanEvent(
            span_id=span.id,
            message=f"Event {i}",
        ))
        trace.root_span = span
        storage.save_trace(trace)

    # Old traces (60 days old)
    for i in range(5):
        trace = Trace(
            id=f"old-{i}",
            agent_name="agent-a",
            name=f"old-task-{i}",
            status=TraceStatus.COMPLETED,
            started_at=now - timedelta(days=60, hours=i),
            ended_at=now - timedelta(days=60, hours=i) + timedelta(seconds=1),
            duration_ms=1000,
        )
        span = Span(
            id=f"span-old-{i}",
            trace_id=trace.id,
            name="root",
            status=TraceStatus.COMPLETED,
            started_at=trace.started_at,
            ended_at=trace.ended_at,
            duration_ms=1000,
        )
        trace.root_span = span
        storage.save_trace(trace)

    # Recent logs
    for i in range(4):
        storage.save_log(LogEntry(
            agent_name="agent-a",
            level=LogLevel.INFO,
            message=f"Recent log {i}",
            timestamp=now - timedelta(days=3),
        ))

    # Old logs
    for i in range(6):
        storage.save_log(LogEntry(
            agent_name="agent-a",
            level=LogLevel.ERROR,
            message=f"Old log {i}",
            timestamp=now - timedelta(days=45),
        ))

    # Health checks
    storage.save_health_check(HealthCheck(
        name="db",
        agent_name="agent-a",
        status=HealthStatus.OK,
        timestamp=now - timedelta(days=2),
    ))
    storage.save_health_check(HealthCheck(
        name="db",
        agent_name="agent-a",
        status=HealthStatus.OK,
        timestamp=now - timedelta(days=40),
    ))

    # Token usage
    storage.save_token_usage(TokenUsage(
        agent_name="agent-a",
        model="gpt-4o",
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        estimated_cost_usd=0.001,
        timestamp=now - timedelta(days=3),
    ))
    storage.save_token_usage(TokenUsage(
        agent_name="agent-a",
        model="gpt-4o",
        input_tokens=200,
        output_tokens=100,
        total_tokens=300,
        estimated_cost_usd=0.002,
        timestamp=now - timedelta(days=50),
    ))

    return storage


class TestPrune:
    """Test data pruning."""

    def test_prune_old_traces(self, aged_storage):
        """Should delete traces older than specified days."""
        result = prune(days=30, storage=aged_storage)

        assert result.traces_deleted == 5  # 5 old traces
        assert result.spans_deleted == 5   # 5 old spans
        assert result.total_deleted > 0

        # Recent traces should remain
        remaining = aged_storage.get_traces()
        assert len(remaining) == 3

    def test_prune_old_logs(self, aged_storage):
        """Should delete logs older than specified days."""
        result = prune(days=30, storage=aged_storage)
        assert result.logs_deleted == 6  # 6 old logs

    def test_prune_old_health(self, aged_storage):
        """Should delete old health checks."""
        result = prune(days=30, storage=aged_storage)
        assert result.health_deleted == 1  # 1 old check

    def test_prune_old_costs(self, aged_storage):
        """Should delete old cost records."""
        result = prune(days=30, storage=aged_storage)
        assert result.cost_deleted == 1  # 1 old record

    def test_prune_dry_run(self, aged_storage):
        """Dry run should count but not delete."""
        result = prune(days=30, storage=aged_storage, dry_run=True)

        # Should report counts
        assert result.traces_deleted == 5
        assert result.logs_deleted == 6

        # But data should still be there
        traces = aged_storage.get_traces(limit=100)
        assert len(traces) == 8  # All still there

    def test_prune_per_type_days(self, aged_storage):
        """Different retention per type."""
        result = prune(
            trace_days=10,   # Delete traces >10 days
            log_days=100,    # Keep all logs
            health_days=100, # Keep all health
            cost_days=100,   # Keep all costs
            storage=aged_storage,
        )

        assert result.traces_deleted == 5  # Old traces gone
        assert result.logs_deleted == 0    # All logs kept
        assert result.health_deleted == 0  # All health kept
        assert result.cost_deleted == 0    # All costs kept

    def test_prune_nothing_old(self, storage):
        """Prune on empty database should do nothing."""
        result = prune(days=30, storage=storage)
        assert result.total_deleted == 0
        assert result.summary() == "Nothing to prune."

    def test_prune_summary(self):
        """PruneResult.summary() should format nicely."""
        result = PruneResult(
            traces_deleted=10,
            spans_deleted=25,
            logs_deleted=100,
        )
        s = result.summary()
        assert "10 traces" in s
        assert "25 spans" in s
        assert "100 logs" in s
        assert "135 total" in s


class TestVacuum:
    """Test database vacuuming."""

    def test_vacuum(self, aged_storage):
        """Vacuum should not crash."""
        # First prune, then vacuum
        prune(days=30, storage=aged_storage)
        saved = vacuum(storage=aged_storage)
        assert isinstance(saved, int)
        assert saved >= 0


class TestDbInfo:
    """Test database info."""

    def test_info_empty(self, storage):
        """Info on empty database."""
        info = db_info(storage=storage)
        assert info.size_bytes > 0
        assert info.path == storage.db_path
        assert "traces" in info.table_counts

    def test_info_with_data(self, aged_storage):
        """Info with data."""
        info = db_info(storage=aged_storage)
        assert info.table_counts["traces"] == 8
        assert info.table_counts["logs"] == 10
        assert info.oldest_trace is not None
        assert info.newest_trace is not None

    def test_info_to_dict(self, aged_storage):
        """Serialization works."""
        info = db_info(storage=aged_storage)
        d = info.to_dict()
        assert isinstance(d["size_mb"], float)
        assert isinstance(d["table_counts"], dict)


class TestExportJsonl:
    """Test JSONL export."""

    def test_export_all(self, aged_storage):
        """Export all data."""
        buf = StringIO()
        count = export_jsonl(buf, storage=aged_storage)

        assert count > 0
        buf.seek(0)
        lines = buf.readlines()
        assert len(lines) == count

        # Each line should be valid JSON with a _type field
        for line in lines:
            obj = json.loads(line)
            assert "_type" in obj
            assert obj["_type"] in ("trace", "span", "log", "health_check", "token_usage")

    def test_export_traces_only(self, aged_storage):
        """Export only traces."""
        buf = StringIO()
        count = export_jsonl(buf, tables=["traces"], storage=aged_storage)

        buf.seek(0)
        types = set()
        for line in buf.readlines():
            obj = json.loads(line)
            types.add(obj["_type"])

        assert "trace" in types
        assert "span" in types  # Spans included with traces
        assert "log" not in types

    def test_export_agent_filter(self, aged_storage):
        """Export filtered by agent."""
        buf = StringIO()
        export_jsonl(buf, agent_name="agent-a", storage=aged_storage)

        buf.seek(0)
        for line in buf.readlines():
            obj = json.loads(line)
            if "agent_name" in obj:
                assert obj["agent_name"] == "agent-a"

    def test_export_to_file(self, aged_storage, tmp_path):
        """Export to a file path."""
        output_path = str(tmp_path / "export.jsonl")
        count = export_jsonl(output_path, storage=aged_storage)
        assert count > 0

        with open(output_path) as f:
            lines = f.readlines()
        assert len(lines) == count

    def test_export_hours_filter(self, aged_storage):
        """Export with time filter."""
        buf = StringIO()
        count_all = export_jsonl(StringIO(), storage=aged_storage)
        count_recent = export_jsonl(buf, hours=168, storage=aged_storage)  # Last 7 days

        # Recent should be less than all
        assert count_recent < count_all
