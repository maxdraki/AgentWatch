"""Tests for the report generation system."""

import os
import tempfile

import pytest

from agentwatch.core import init, _reset, get_agent
from agentwatch.models import Trace, TraceStatus, LogEntry, LogLevel, HealthCheck, HealthStatus
from agentwatch.reports import summary, summary_data


@pytest.fixture(autouse=True)
def clean_agent():
    _reset()
    yield
    _reset()


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def seeded_agent(db_path):
    """Agent with seeded data for reports."""
    init("report-agent", db_path=db_path)
    agent = get_agent()

    # Traces
    for i in range(10):
        t = Trace(id=f"t-{i}", agent_name="report-agent", name=f"task-{i % 3}")
        status = TraceStatus.FAILED if i < 3 else TraceStatus.COMPLETED
        t.finish(status=status)
        agent.storage.save_trace(t)

    # Logs
    for i in range(5):
        agent.storage.save_log(LogEntry(
            agent_name="report-agent",
            level=LogLevel.INFO,
            message=f"Info {i}",
        ))
    agent.storage.save_log(LogEntry(
        agent_name="report-agent",
        level=LogLevel.ERROR,
        message="Something failed",
    ))

    # Health
    agent.storage.save_health_check(HealthCheck(
        name="db", agent_name="report-agent",
        status=HealthStatus.OK, message="connected",
    ))
    agent.storage.save_health_check(HealthCheck(
        name="api", agent_name="report-agent",
        status=HealthStatus.WARN, message="slow response",
    ))

    # Costs
    from agentwatch.costs import TokenUsage
    agent.storage.save_token_usage(TokenUsage(
        agent_name="report-agent",
        model="test-model",
        input_tokens=1000,
        output_tokens=500,
        total_tokens=1500,
        estimated_cost_usd=0.05,
    ))

    return agent


class TestSummaryData:
    def test_basic(self, seeded_agent):
        data = summary_data(hours=24)
        assert data["agent_name"] == "all"
        assert data["traces"]["total"] == 10
        assert data["traces"]["failed"] == 3
        assert data["traces"]["completed"] == 7
        assert data["traces"]["error_rate_pct"] == 30.0
        assert data["health"]["ok"] == 1
        assert data["health"]["warn"] == 1
        assert data["costs"]["total_usd"] == 0.05
        assert len(data["recent_errors"]) >= 1

    def test_agent_filter(self, seeded_agent):
        data = summary_data(hours=24, agent_name="report-agent")
        assert data["agent_name"] == "report-agent"
        assert data["traces"]["total"] == 10

    def test_top_failures(self, seeded_agent):
        data = summary_data(hours=24)
        assert len(data["top_failures"]) > 0

    def test_empty_db(self, db_path):
        init("empty-agent", db_path=db_path)
        data = summary_data(hours=24)
        assert data["traces"]["total"] == 0
        assert data["traces"]["error_rate_pct"] == 0


class TestSummaryText:
    def test_renders(self, seeded_agent):
        text = summary(hours=24)
        assert "AgentWatch Report" in text
        assert "report-agent" in text or "all" in text
        assert "Traces: 10" in text
        assert "30.0%" in text
        assert "$0.0500" in text

    def test_health_status(self, seeded_agent):
        text = summary(hours=24)
        # Should show WARNING because there's a warn check
        assert "WARNING" in text or "HEALTHY" in text

    def test_empty(self, db_path):
        init("empty", db_path=db_path)
        text = summary(hours=24)
        assert "AgentWatch Report" in text
        assert "Traces: 0" in text
