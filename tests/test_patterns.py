"""Tests for the pattern detection engine."""

import os
import tempfile
import time
from datetime import datetime, timezone, timedelta

import pytest

from agentwatch.core import init, _reset
from agentwatch.models import Trace, Span, TraceStatus, LogEntry, LogLevel, HealthCheck, HealthStatus
from agentwatch.patterns import (
    Pattern,
    PatternType,
    Severity,
    TrendDirection,
    TrendAnalysis,
    detect_patterns,
    detect_trends,
    _detect_recurring_errors,
    _detect_performance_degradation,
    _detect_error_spikes,
    _detect_slow_traces,
    _compute_duration_trend,
    _compute_health_trend,
    _compute_overall_direction,
)


@pytest.fixture(autouse=True)
def clean_state():
    """Reset global state and use a temp DB for each test."""
    _reset()
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init("test-agent", db_path=path)
    yield path
    _reset()
    os.unlink(path)


def _make_trace(name="test-task", status=TraceStatus.COMPLETED, duration_ms=100, minutes_ago=0):
    """Helper to create a trace dict (as returned by storage)."""
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        "id": f"t-{name}-{minutes_ago}",
        "agent_name": "test-agent",
        "name": name,
        "status": status.value,
        "started_at": ts.isoformat(),
        "ended_at": (ts + timedelta(milliseconds=duration_ms)).isoformat(),
        "duration_ms": duration_ms,
    }


def _make_log(message="test error", level="error", minutes_ago=0):
    """Helper to create a log dict."""
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        "id": f"l-{minutes_ago}",
        "agent_name": "test-agent",
        "level": level,
        "message": message,
        "timestamp": ts.isoformat(),
        "trace_id": None,
        "span_id": None,
    }


class TestRecurringErrors:
    def test_no_errors(self):
        traces = [_make_trace(status=TraceStatus.COMPLETED) for _ in range(5)]
        patterns = _detect_recurring_errors(traces, [], min_occurrences=3)
        assert len(patterns) == 0

    def test_recurring_trace_failures(self):
        traces = [_make_trace(name="email-send", status=TraceStatus.FAILED, minutes_ago=i) for i in range(5)]
        patterns = _detect_recurring_errors(traces, [], min_occurrences=3)
        assert len(patterns) == 1
        assert patterns[0].type == PatternType.RECURRING_ERROR
        assert patterns[0].occurrences == 5
        assert "email-send" in patterns[0].title

    def test_below_threshold(self):
        traces = [_make_trace(name="flaky", status=TraceStatus.FAILED, minutes_ago=i) for i in range(2)]
        patterns = _detect_recurring_errors(traces, [], min_occurrences=3)
        assert len(patterns) == 0

    def test_recurring_log_errors(self):
        logs = [_make_log(message="Connection refused to API server", minutes_ago=i) for i in range(4)]
        patterns = _detect_recurring_errors([], logs, min_occurrences=3)
        assert len(patterns) == 1
        assert "Connection refused" in patterns[0].title

    def test_critical_severity_for_high_count(self):
        # >= 2x min_occurrences should be critical
        traces = [_make_trace(name="bad-task", status=TraceStatus.FAILED, minutes_ago=i) for i in range(8)]
        patterns = _detect_recurring_errors(traces, [], min_occurrences=3)
        assert patterns[0].severity == Severity.CRITICAL


class TestPerformanceDegradation:
    def test_no_degradation(self):
        traces = [_make_trace(duration_ms=100, minutes_ago=i) for i in range(10)]
        patterns = _detect_performance_degradation(traces)
        assert len(patterns) == 0

    def test_degradation_detected(self):
        # Older traces fast, newer traces slow
        older = [_make_trace(name="api-call", duration_ms=100, minutes_ago=60-i) for i in range(5)]
        newer = [_make_trace(name="api-call", duration_ms=300, minutes_ago=5-i) for i in range(5)]
        patterns = _detect_performance_degradation(older + newer)
        assert len(patterns) == 1
        assert patterns[0].type == PatternType.PERFORMANCE_DEGRADATION
        assert "api-call" in patterns[0].title

    def test_insufficient_data(self):
        traces = [_make_trace(duration_ms=100, minutes_ago=i) for i in range(3)]
        patterns = _detect_performance_degradation(traces)
        assert len(patterns) == 0


class TestErrorSpikes:
    def test_no_spike(self):
        traces = [_make_trace(minutes_ago=i) for i in range(20)]
        patterns = _detect_error_spikes(traces, window_hours=24)
        assert len(patterns) == 0

    def test_spike_detected(self):
        # First 15: ok. Last 5: all failed
        older = [_make_trace(minutes_ago=60-i) for i in range(15)]
        recent = [_make_trace(status=TraceStatus.FAILED, minutes_ago=5-i) for i in range(5)]
        patterns = _detect_error_spikes(older + recent, window_hours=24)
        assert len(patterns) == 1
        assert patterns[0].type == PatternType.ERROR_SPIKE

    def test_insufficient_data(self):
        traces = [_make_trace(minutes_ago=i) for i in range(3)]
        patterns = _detect_error_spikes(traces, window_hours=24)
        assert len(patterns) == 0


class TestSlowTraces:
    def test_no_outliers(self):
        traces = [_make_trace(duration_ms=100, minutes_ago=i) for i in range(10)]
        patterns = _detect_slow_traces(traces)
        assert len(patterns) == 0

    def test_outlier_detected(self):
        normal = [_make_trace(name="task", duration_ms=100, minutes_ago=i) for i in range(10)]
        slow = [_make_trace(name="slow-task", duration_ms=10000, minutes_ago=11)]
        patterns = _detect_slow_traces(normal + slow)
        assert len(patterns) >= 1
        assert patterns[0].type == PatternType.SLOW_TRACE

    def test_insufficient_data(self):
        traces = [_make_trace(duration_ms=100)]
        patterns = _detect_slow_traces(traces)
        assert len(patterns) == 0


class TestDurationTrend:
    def test_stable(self):
        traces = [_make_trace(duration_ms=100, minutes_ago=i) for i in range(10)]
        assert _compute_duration_trend(traces) == TrendDirection.STABLE

    def test_degrading(self):
        older = [_make_trace(duration_ms=100, minutes_ago=60-i) for i in range(5)]
        newer = [_make_trace(duration_ms=200, minutes_ago=5-i) for i in range(5)]
        assert _compute_duration_trend(older + newer) == TrendDirection.DEGRADING

    def test_improving(self):
        older = [_make_trace(duration_ms=300, minutes_ago=60-i) for i in range(5)]
        newer = [_make_trace(duration_ms=100, minutes_ago=5-i) for i in range(5)]
        assert _compute_duration_trend(older + newer) == TrendDirection.IMPROVING

    def test_insufficient_data(self):
        traces = [_make_trace(duration_ms=100)]
        assert _compute_duration_trend(traces) is None


class TestHealthTrend:
    def test_all_ok(self):
        health = [{"status": "ok"}, {"status": "ok"}, {"status": "ok"}]
        assert _compute_health_trend(health) == TrendDirection.STABLE

    def test_degrading(self):
        health = [{"status": "critical"}, {"status": "critical"}, {"status": "ok"}]
        assert _compute_health_trend(health) == TrendDirection.DEGRADING

    def test_empty(self):
        assert _compute_health_trend([]) is None


class TestOverallDirection:
    def test_stable(self):
        d = _compute_overall_direction(2.0, TrendDirection.STABLE, TrendDirection.STABLE, [])
        assert d == TrendDirection.STABLE

    def test_degrading_high_errors(self):
        d = _compute_overall_direction(40.0, TrendDirection.STABLE, TrendDirection.STABLE, [])
        assert d == TrendDirection.DEGRADING

    def test_degrading_health(self):
        d = _compute_overall_direction(2.0, TrendDirection.STABLE, TrendDirection.DEGRADING, [])
        assert d == TrendDirection.DEGRADING


class TestPatternModel:
    def test_to_dict(self):
        p = Pattern(
            type=PatternType.RECURRING_ERROR,
            severity=Severity.WARN,
            title="Test",
            description="A test pattern",
            occurrences=5,
        )
        d = p.to_dict()
        assert d["type"] == "recurring_error"
        assert d["severity"] == "warn"
        assert d["occurrences"] == 5


class TestTrendAnalysis:
    def test_to_dict(self):
        t = TrendAnalysis(
            direction=TrendDirection.STABLE,
            error_rate=2.5,
            avg_duration_ms=150.0,
            duration_trend=TrendDirection.STABLE,
            health_trend=TrendDirection.STABLE,
            summary="All good",
        )
        d = t.to_dict()
        assert d["direction"] == "stable"
        assert d["error_rate"] == 2.5


class TestIntegration:
    def test_detect_patterns_empty_db(self, clean_state):
        """detect_patterns should work on an empty database."""
        patterns = detect_patterns(agent_name="test-agent")
        assert isinstance(patterns, list)
        assert len(patterns) == 0

    def test_detect_trends_empty_db(self, clean_state):
        """detect_trends should work on an empty database."""
        trends = detect_trends(agent_name="test-agent")
        assert isinstance(trends, TrendAnalysis)
        assert trends.direction == TrendDirection.STABLE
        assert trends.error_rate == 0.0

    def test_detect_patterns_with_data(self, clean_state):
        """Seed the DB with some failed traces and check detection."""
        from agentwatch.core import get_agent
        agent = get_agent()

        # Create 5 failed traces with same name
        for i in range(5):
            t = Trace(
                id=f"fail-{i}",
                agent_name="test-agent",
                name="broken-task",
                status=TraceStatus.FAILED,
            )
            t.finish(status=TraceStatus.FAILED)
            agent.storage.save_trace(t)

        patterns = detect_patterns(agent_name="test-agent", min_occurrences=3)
        assert len(patterns) >= 1
        assert any(p.type == PatternType.RECURRING_ERROR for p in patterns)
