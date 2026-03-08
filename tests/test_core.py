"""Tests for AgentWatch core initialisation."""

import pytest

from agentwatch.core import init, get_agent, shutdown, _reset


@pytest.fixture(autouse=True)
def clean_state():
    """Reset global state before/after each test."""
    _reset()
    yield
    _reset()


def test_init_creates_agent(tmp_path):
    agent = init("test-agent", db_path=str(tmp_path / "test.db"))
    assert agent.name == "test-agent"
    assert agent._active


def test_get_agent_after_init(tmp_path):
    init("test-agent", db_path=str(tmp_path / "test.db"))
    agent = get_agent()
    assert agent.name == "test-agent"


def test_get_agent_before_init():
    with pytest.raises(RuntimeError, match="not initialised"):
        get_agent()


def test_init_same_name_returns_same(tmp_path):
    a1 = init("test", db_path=str(tmp_path / "test.db"))
    a2 = init("test")
    assert a1 is a2


def test_init_different_name_raises(tmp_path):
    init("agent-a", db_path=str(tmp_path / "test.db"))
    with pytest.raises(RuntimeError, match="already initialised"):
        init("agent-b")


def test_shutdown(tmp_path):
    init("test", db_path=str(tmp_path / "test.db"))
    shutdown()
    with pytest.raises(RuntimeError):
        get_agent()


def test_reinit_after_shutdown(tmp_path):
    init("agent-a", db_path=str(tmp_path / "a.db"))
    shutdown()
    agent = init("agent-b", db_path=str(tmp_path / "b.db"))
    assert agent.name == "agent-b"
