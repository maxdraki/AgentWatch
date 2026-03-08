"""Tests for AgentWatch tracing."""

import pytest
import time

from agentwatch.core import init, _reset
from agentwatch.tracing import trace


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    _reset()
    init("test-agent", db_path=str(tmp_path / "test.db"))
    yield
    _reset()


def test_trace_context_manager():
    with trace("my-task") as span:
        assert span.name == "my-task"
        span.event("step 1")

    assert span.status.value == "completed"
    assert span.duration_ms is not None


def test_trace_captures_error():
    with pytest.raises(ValueError, match="oops"):
        with trace("failing-task") as span:
            raise ValueError("oops")

    assert span.status.value == "failed"


def test_trace_events():
    with trace("my-task") as span:
        span.event("found items", {"count": 5})
        span.event("processed items")

    # Events are on the underlying span
    assert len(span._span.events) == 2
    assert span._span.events[0].message == "found items"


def test_trace_metadata():
    with trace("my-task", metadata={"model": "gpt-4"}) as span:
        span.set_metadata("tokens", 150)

    assert span._span.metadata["model"] == "gpt-4"
    assert span._span.metadata["tokens"] == 150


def test_trace_persisted(tmp_path):
    from agentwatch.core import get_agent

    with trace("persisted-task") as span:
        span.event("did something")

    agent = get_agent()
    stored = agent.storage.get_trace(span.trace_id)
    assert stored is not None
    assert stored["name"] == "persisted-task"
    assert len(stored["spans"]) == 1


def test_trace_as_decorator():
    @trace("decorated")
    def my_func(x, y):
        return x + y

    result = my_func(2, 3)
    assert result == 5


def test_bare_trace_decorator():
    @trace
    def another_func():
        return 42

    assert another_func() == 42
