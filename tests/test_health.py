"""Tests for AgentWatch health checks."""

import pytest

from agentwatch.core import init, _reset
from agentwatch import health
from agentwatch.models import HealthStatus


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    _reset()
    init("test-agent", db_path=str(tmp_path / "test.db"))
    yield
    _reset()


def test_register_and_run():
    health.register("simple", lambda: True)
    result = health.run("simple")
    assert result.status == HealthStatus.OK


def test_check_returns_false():
    health.register("failing", lambda: False)
    result = health.run("failing")
    assert result.status == HealthStatus.CRITICAL


def test_check_raises_exception():
    def bad_check():
        raise ConnectionError("cannot connect")

    health.register("broken", bad_check)
    result = health.run("broken")
    assert result.status == HealthStatus.CRITICAL
    assert "ConnectionError" in result.message


def test_check_returns_dict():
    health.register("detailed", lambda: {"status": "warn", "message": "slow", "latency": 500})
    result = health.run("detailed")
    assert result.status == HealthStatus.WARN
    assert result.message == "slow"
    assert result.metadata["latency"] == 500


def test_run_all():
    health.register("a", lambda: True)
    health.register("b", lambda: True)
    health.register("c", lambda: False)
    results = health.run_all()
    assert len(results) == 3

    statuses = {r.name: r.status for r in results}
    assert statuses["a"] == HealthStatus.OK
    assert statuses["c"] == HealthStatus.CRITICAL


def test_status_overall():
    health.register("ok-check", lambda: True)
    health.register("bad-check", lambda: False)
    s = health.status()
    assert s["overall"] == "critical"
    assert s["check_count"] == 2


def test_unregistered_check():
    with pytest.raises(KeyError, match="nonexistent"):
        health.run("nonexistent")


def test_health_persisted(tmp_path):
    from agentwatch.core import get_agent

    health.register("db", lambda: True)
    health.run("db")

    agent = get_agent()
    latest = agent.storage.get_health_latest()
    assert len(latest) == 1
    assert latest[0]["name"] == "db"


def test_duration_measured():
    import time

    def slow_check():
        time.sleep(0.05)
        return True

    health.register("slow", slow_check)
    result = health.run("slow")
    assert result.duration_ms is not None
    assert result.duration_ms >= 40  # at least 40ms
