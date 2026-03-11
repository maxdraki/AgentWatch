"""Tests for the custom metrics system."""

import os
import tempfile

import pytest

import agentwatch
from agentwatch.metrics import MetricPoint, record, query, summary, list_metrics
from agentwatch.storage import Storage


@pytest.fixture
def storage():
    """Create a temporary storage instance."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = Storage(db_path=path)
    yield s
    s.close()
    os.unlink(path)


@pytest.fixture
def agent(storage):
    """Initialise an agent with the test storage."""
    agentwatch.init("test-agent", db_path=storage.db_path)
    yield
    from agentwatch.core import _reset
    _reset()


class TestMetricPoint:
    """Test MetricPoint model."""

    def test_create_default(self):
        point = MetricPoint(name="queue_depth", value=42.0)
        assert point.name == "queue_depth"
        assert point.value == 42.0
        assert point.kind == "gauge"
        assert point.tags == {}
        assert point.id
        assert point.timestamp

    def test_to_dict(self):
        point = MetricPoint(
            name="requests",
            value=100.0,
            kind="counter",
            tags={"method": "POST"},
            agent_name="test",
        )
        d = point.to_dict()
        assert d["name"] == "requests"
        assert d["value"] == 100.0
        assert d["kind"] == "counter"
        assert d["tags"] == {"method": "POST"}
        assert d["agent_name"] == "test"

    def test_counter_kind(self):
        point = MetricPoint(name="errors", value=5.0, kind="counter")
        assert point.kind == "counter"


class TestMetricStorage:
    """Test metric persistence in SQLite."""

    def test_save_and_query(self, storage):
        point = MetricPoint(
            agent_name="test-agent",
            name="queue_depth",
            value=42.0,
        )
        storage.save_metric(point)

        results = storage.get_metrics(name="queue_depth")
        assert len(results) == 1
        assert results[0]["value"] == 42.0
        assert results[0]["name"] == "queue_depth"

    def test_query_by_agent(self, storage):
        for name in ("agent-a", "agent-b"):
            point = MetricPoint(agent_name=name, name="x", value=1.0)
            storage.save_metric(point)

        results = storage.get_metrics(agent_name="agent-a")
        assert len(results) == 1
        assert results[0]["agent_name"] == "agent-a"

    def test_query_by_tags(self, storage):
        p1 = MetricPoint(
            agent_name="test", name="requests", value=10.0,
            tags={"method": "GET", "status": "200"},
        )
        p2 = MetricPoint(
            agent_name="test", name="requests", value=5.0,
            tags={"method": "POST", "status": "500"},
        )
        storage.save_metric(p1)
        storage.save_metric(p2)

        results = storage.get_metrics(name="requests", tags={"method": "GET"})
        assert len(results) == 1
        assert results[0]["tags"]["method"] == "GET"

    def test_query_by_multiple_tags(self, storage):
        p1 = MetricPoint(
            agent_name="test", name="req", value=1.0,
            tags={"method": "GET", "status": "200"},
        )
        p2 = MetricPoint(
            agent_name="test", name="req", value=2.0,
            tags={"method": "GET", "status": "500"},
        )
        storage.save_metric(p1)
        storage.save_metric(p2)

        results = storage.get_metrics(
            name="req", tags={"method": "GET", "status": "200"}
        )
        assert len(results) == 1
        assert results[0]["value"] == 1.0

    def test_metric_summary(self, storage):
        for v in (10.0, 20.0, 30.0, 40.0, 50.0):
            point = MetricPoint(agent_name="test", name="latency", value=v)
            storage.save_metric(point)

        s = storage.get_metric_summary("latency")
        assert s["count"] == 5
        assert s["min"] == 10.0
        assert s["max"] == 50.0
        assert s["avg"] == 30.0
        assert s["sum"] == 150.0
        assert s["latest_value"] == 50.0
        assert len(s["series"]) == 5

    def test_list_metrics(self, storage):
        for name in ("cpu", "memory", "disk"):
            point = MetricPoint(agent_name="test", name=name, value=42.0)
            storage.save_metric(point)

        metrics = storage.list_metrics()
        names = [m["name"] for m in metrics]
        assert "cpu" in names
        assert "memory" in names
        assert "disk" in names

    def test_list_metrics_by_agent(self, storage):
        storage.save_metric(MetricPoint(agent_name="a", name="x", value=1.0))
        storage.save_metric(MetricPoint(agent_name="b", name="y", value=2.0))

        metrics = storage.list_metrics(agent_name="a")
        assert len(metrics) == 1
        assert metrics[0]["name"] == "x"

    def test_stats_includes_metrics(self, storage):
        # Save a trace to create the agent
        from agentwatch.models import Trace, TraceStatus
        trace = Trace(agent_name="test", name="t", status=TraceStatus.COMPLETED)
        storage.save_trace(trace)

        storage.save_metric(MetricPoint(agent_name="test", name="x", value=1.0))
        storage.save_metric(MetricPoint(agent_name="test", name="y", value=2.0))

        stats = storage.get_stats(agent_name="test")
        assert stats["total_metrics"] == 2


class TestMetricRecordFunction:
    """Test the high-level record() function."""

    def test_record_basic(self, agent):
        point = record("queue_depth", 42)
        assert point.name == "queue_depth"
        assert point.value == 42.0
        assert point.agent_name == "test-agent"
        assert point.kind == "gauge"

    def test_record_with_tags(self, agent):
        point = record("requests", 1, tags={"method": "GET"})
        assert point.tags == {"method": "GET"}

    def test_record_counter(self, agent):
        point = record("errors", 5, kind="counter")
        assert point.kind == "counter"

    def test_record_captures_trace_context(self, agent):
        with agentwatch.trace("test-trace") as span:
            point = record("inside_trace", 99)
            assert point.trace_id == span.trace_id
            assert point.span_id == span.id

    def test_record_no_trace_context(self, agent):
        point = record("no_context", 1)
        assert point.trace_id is None

    def test_agentwatch_metric_shorthand(self, agent):
        """Test that agentwatch.metric() works as a shorthand."""
        point = agentwatch.metric("shorthand_test", 123)
        assert point.name == "shorthand_test"
        assert point.value == 123.0

    def test_query_recorded_metrics(self, agent):
        record("cpu", 50.0)
        record("cpu", 75.0)
        record("memory", 80.0)

        results = query("cpu")
        assert len(results) == 2

    def test_summary_of_recorded_metrics(self, agent):
        for v in (10.0, 20.0, 30.0):
            record("latency_ms", v)

        s = summary("latency_ms")
        assert s["count"] == 3
        assert s["avg"] == 20.0

    def test_list_recorded_metrics(self, agent):
        record("a", 1)
        record("b", 2)

        metrics = list_metrics()
        names = [m["name"] for m in metrics]
        assert "a" in names
        assert "b" in names
