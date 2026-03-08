"""Tests for AgentWatch structured logging."""

import pytest

from agentwatch.core import init, get_agent, _reset
from agentwatch.logging import log
from agentwatch.tracing import trace
from agentwatch.models import LogLevel


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    _reset()
    init("test-agent", db_path=str(tmp_path / "test.db"))
    yield
    _reset()


def test_basic_log():
    entry = log("info", "hello world")
    assert entry.level == LogLevel.INFO
    assert entry.message == "hello world"
    assert entry.agent_name == "test-agent"


def test_log_with_metadata():
    entry = log("error", "API failed", {"status": 500, "url": "https://api.example.com"})
    assert entry.metadata["status"] == 500


def test_log_persisted():
    log("warn", "disk getting full")
    agent = get_agent()
    logs = agent.storage.get_logs()
    assert len(logs) == 1
    assert logs[0]["level"] == "warn"


def test_log_auto_links_to_trace():
    with trace("my-task") as span:
        entry = log("info", "inside trace")

    assert entry.trace_id == span.trace_id
    assert entry.span_id is not None


def test_log_outside_trace():
    entry = log("info", "no trace context")
    assert entry.trace_id is None
    assert entry.span_id is None


def test_invalid_level():
    with pytest.raises(ValueError, match="Unknown log level"):
        log("banana", "this should fail")


def test_all_levels():
    for level in ["debug", "info", "warn", "warning", "error", "critical"]:
        entry = log(level, f"test {level}")
        assert entry.level is not None
