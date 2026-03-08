"""Tests for the Prometheus/OpenMetrics exporter."""

import pytest
from datetime import datetime, timezone, timedelta

from agentwatch.core import init, _reset
from agentwatch.storage import Storage
from agentwatch.models import (
    Trace, Span, LogEntry, HealthCheck,
    TraceStatus, LogLevel, HealthStatus,
)
from agentwatch.costs import TokenUsage
from agentwatch.exporters.prometheus import PrometheusExporter, _escape_label, _metric_line


@pytest.fixture
def storage(tmp_path):
    """Create a temporary storage instance."""
    db_path = str(tmp_path / "test.db")
    return Storage(db_path=db_path)


@pytest.fixture
def exporter(storage):
    """Create an exporter with test storage."""
    return PrometheusExporter(storage)


@pytest.fixture
def seeded_storage(storage):
    """Storage with realistic test data."""
    now = datetime.now(timezone.utc)

    # Traces
    for i in range(10):
        status = TraceStatus.COMPLETED if i < 7 else TraceStatus.FAILED
        trace = Trace(
            id=f"trace-{i}",
            agent_name="test-agent",
            name=f"task-{i}",
            status=status,
            started_at=now - timedelta(hours=i),
            ended_at=now - timedelta(hours=i) + timedelta(seconds=i + 1),
            duration_ms=(i + 1) * 1000,
        )
        storage.save_trace(trace)

    # Second agent
    for i in range(5):
        trace = Trace(
            id=f"trace-b-{i}",
            agent_name="other-agent",
            name=f"job-{i}",
            status=TraceStatus.COMPLETED,
            started_at=now - timedelta(hours=i),
            ended_at=now - timedelta(hours=i) + timedelta(seconds=2),
            duration_ms=2000,
        )
        storage.save_trace(trace)

    # Logs
    for level in (LogLevel.INFO, LogLevel.ERROR, LogLevel.WARN):
        storage.save_log(LogEntry(
            agent_name="test-agent",
            level=level,
            message=f"Test {level.value} log",
        ))

    # Health checks
    storage.save_health_check(HealthCheck(
        name="database",
        agent_name="test-agent",
        status=HealthStatus.OK,
        message="Connected",
        duration_ms=5.2,
    ))
    storage.save_health_check(HealthCheck(
        name="disk",
        agent_name="test-agent",
        status=HealthStatus.WARN,
        message="85% used",
        duration_ms=1.1,
    ))

    # Token usage
    storage.save_token_usage(TokenUsage(
        agent_name="test-agent",
        model="claude-sonnet-4-20250514",
        input_tokens=1000,
        output_tokens=500,
        total_tokens=1500,
        estimated_cost_usd=0.0105,
    ))

    return storage


class TestEscaping:
    """Test Prometheus label escaping."""

    def test_escape_quotes(self):
        assert _escape_label('say "hello"') == 'say \\"hello\\"'

    def test_escape_backslash(self):
        assert _escape_label("path\\to") == "path\\\\to"

    def test_escape_newline(self):
        assert _escape_label("line1\nline2") == "line1\\nline2"

    def test_no_escape_needed(self):
        assert _escape_label("simple-label") == "simple-label"


class TestMetricLine:
    """Test metric line formatting."""

    def test_simple_metric(self):
        line = _metric_line("my_metric", 42)
        assert line == "my_metric 42"

    def test_metric_with_labels(self):
        line = _metric_line("my_metric", 42, {"agent": "test", "status": "ok"})
        assert 'agent="test"' in line
        assert 'status="ok"' in line
        assert line.startswith("my_metric{")
        assert line.endswith("} 42")

    def test_metric_with_timestamp(self):
        line = _metric_line("my_metric", 42, timestamp_ms=1234567890)
        assert line == "my_metric 42 1234567890"

    def test_float_value(self):
        line = _metric_line("my_metric", 3.14)
        assert line == "my_metric 3.14"


class TestPrometheusExporter:
    """Test the full exporter."""

    def test_collect_empty(self, exporter):
        """Should produce valid output even with no data."""
        output = exporter.collect()
        assert isinstance(output, str)
        # Should have TYPE and HELP lines
        assert "# TYPE" in output or output.strip() == ""

    def test_collect_with_data(self, seeded_storage):
        """Should produce metrics for seeded data."""
        exporter = PrometheusExporter(seeded_storage)
        output = exporter.collect()

        # Check trace metrics
        assert "agentwatch_traces_total" in output
        assert 'agent="test-agent"' in output
        assert 'status="completed"' in output
        assert 'status="failed"' in output

        # Check log metrics
        assert "agentwatch_logs_total" in output

        # Check health metrics
        assert "agentwatch_health_status" in output
        assert 'check="database"' in output
        assert 'check="disk"' in output

        # Check cost metrics
        assert "agentwatch_tokens_total" in output
        assert "agentwatch_cost_usd_total" in output

        # Check agent info
        assert "agentwatch_agent_info" in output

    def test_health_status_values(self, seeded_storage):
        """Health statuses should map to correct numeric values."""
        exporter = PrometheusExporter(seeded_storage)
        output = exporter.collect()

        lines = output.split("\n")
        health_lines = [l for l in lines if l.startswith("agentwatch_health_status{")]

        # Find the database check (should be ok = 1)
        db_line = [l for l in health_lines if 'check="database"' in l]
        assert len(db_line) == 1
        assert db_line[0].endswith(" 1")

        # Find the disk check (should be warn = 0.5)
        disk_line = [l for l in health_lines if 'check="disk"' in l]
        assert len(disk_line) == 1
        assert disk_line[0].endswith(" 0.5")

    def test_type_declarations(self, seeded_storage):
        """Output should include TYPE declarations."""
        exporter = PrometheusExporter(seeded_storage)
        output = exporter.collect()

        assert "# TYPE agentwatch_traces_total counter" in output
        assert "# TYPE agentwatch_health_status gauge" in output
        assert "# TYPE agentwatch_tokens_total counter" in output

    def test_help_declarations(self, seeded_storage):
        """Output should include HELP declarations."""
        exporter = PrometheusExporter(seeded_storage)
        output = exporter.collect()

        assert "# HELP agentwatch_traces_total" in output
        assert "# HELP agentwatch_health_status" in output

    def test_multi_agent(self, seeded_storage):
        """Should include metrics for all agents."""
        exporter = PrometheusExporter(seeded_storage)
        output = exporter.collect()

        assert 'agent="test-agent"' in output
        assert 'agent="other-agent"' in output

    def test_error_rate(self, seeded_storage):
        """Should include error rate metrics."""
        exporter = PrometheusExporter(seeded_storage)
        output = exporter.collect()

        assert "agentwatch_error_rate_pct" in output

    def test_duration_avg(self, seeded_storage):
        """Should include average duration metrics."""
        exporter = PrometheusExporter(seeded_storage)
        output = exporter.collect()

        assert "agentwatch_trace_duration_seconds_avg" in output

    def test_token_direction_labels(self, seeded_storage):
        """Token metrics should have input/output direction labels."""
        exporter = PrometheusExporter(seeded_storage)
        output = exporter.collect()

        assert 'direction="input"' in output
        assert 'direction="output"' in output
