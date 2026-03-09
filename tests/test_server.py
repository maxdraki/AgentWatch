"""Tests for the web dashboard server."""

import os
import tempfile

import pytest

from agentwatch.core import init, _reset
from agentwatch.models import Trace, TraceStatus, LogEntry, LogLevel, HealthCheck, HealthStatus

# Skip all tests if FastAPI not installed
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from agentwatch.server.app import create_app


@pytest.fixture()
def client():
    """Create a test client with a temp database."""
    _reset()
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    app = create_app(db_path=path)
    client = TestClient(app)

    # Also init the SDK pointing at the same DB for seeding
    init("test-agent", db_path=path)

    yield client

    _reset()
    os.unlink(path)


@pytest.fixture()
def seeded_client(client):
    """Client with some test data seeded."""
    from agentwatch.core import get_agent
    agent = get_agent()

    # Seed traces
    for i in range(3):
        t = Trace(
            id=f"trace-{i}",
            agent_name="test-agent",
            name=f"task-{i}",
            status=TraceStatus.COMPLETED,
        )
        t.finish()
        agent.storage.save_trace(t)

    # Seed a failed trace
    t = Trace(
        id="trace-fail",
        agent_name="test-agent",
        name="failing-task",
        status=TraceStatus.FAILED,
    )
    t.finish(status=TraceStatus.FAILED)
    agent.storage.save_trace(t)

    # Seed logs
    for i in range(5):
        entry = LogEntry(
            agent_name="test-agent",
            level=LogLevel.INFO,
            message=f"Log message {i}",
        )
        agent.storage.save_log(entry)

    entry = LogEntry(
        agent_name="test-agent",
        level=LogLevel.ERROR,
        message="Something broke",
    )
    agent.storage.save_log(entry)

    # Seed health checks
    check = HealthCheck(
        name="database",
        agent_name="test-agent",
        status=HealthStatus.OK,
        message="healthy",
        duration_ms=5.2,
    )
    agent.storage.save_health_check(check)

    # Seed token usage
    from agentwatch.costs import TokenUsage
    usage = TokenUsage(
        agent_name="test-agent",
        model="gpt-4o",
        input_tokens=1000,
        output_tokens=500,
        total_tokens=1500,
        estimated_cost_usd=0.0075,
    )
    agent.storage.save_token_usage(usage)

    return client


class TestWaterfallComputation:
    """Tests for the waterfall layout calculation."""

    def test_empty_trace(self):
        from agentwatch.server.app import _compute_waterfall
        result = _compute_waterfall({"started_at": "2026-01-01T00:00:00+00:00", "spans": []})
        assert result == []

    def test_single_span(self):
        from agentwatch.server.app import _compute_waterfall
        trace = {
            "started_at": "2026-01-01T00:00:00+00:00",
            "duration_ms": 100.0,
            "spans": [{
                "id": "s1",
                "parent_id": None,
                "name": "root",
                "started_at": "2026-01-01T00:00:00+00:00",
                "duration_ms": 100.0,
                "status": "completed",
                "events": [],
            }],
        }
        result = _compute_waterfall(trace)
        assert len(result) == 1
        assert result[0]["depth"] == 0
        assert result[0]["offset_pct"] == 0.0
        assert result[0]["width_pct"] == 100.0

    def test_nested_spans(self):
        from agentwatch.server.app import _compute_waterfall
        trace = {
            "started_at": "2026-01-01T00:00:00+00:00",
            "duration_ms": 200.0,
            "spans": [
                {
                    "id": "s1", "parent_id": None, "name": "root",
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "duration_ms": 200.0, "status": "completed", "events": [],
                },
                {
                    "id": "s2", "parent_id": "s1", "name": "child",
                    "started_at": "2026-01-01T00:00:00.050+00:00",
                    "duration_ms": 50.0, "status": "completed", "events": [],
                },
                {
                    "id": "s3", "parent_id": "s1", "name": "child2",
                    "started_at": "2026-01-01T00:00:00.100+00:00",
                    "duration_ms": 80.0, "status": "completed", "events": [],
                },
            ],
        }
        result = _compute_waterfall(trace)
        assert len(result) == 3

        # Root: depth 0, starts at 0%
        assert result[0]["depth"] == 0
        assert result[0]["offset_pct"] == 0.0

        # Child 1: depth 1, starts at 25% (50ms / 200ms)
        assert result[1]["depth"] == 1
        assert result[1]["offset_pct"] == 25.0
        assert result[1]["width_pct"] == 25.0  # 50ms / 200ms

        # Child 2: depth 1, starts at 50% (100ms / 200ms)
        assert result[2]["depth"] == 1
        assert result[2]["offset_pct"] == 50.0
        assert result[2]["width_pct"] == 40.0  # 80ms / 200ms

    def test_deeply_nested(self):
        from agentwatch.server.app import _compute_waterfall
        trace = {
            "started_at": "2026-01-01T00:00:00+00:00",
            "duration_ms": 100.0,
            "spans": [
                {"id": "s1", "parent_id": None, "name": "l0",
                 "started_at": "2026-01-01T00:00:00+00:00", "duration_ms": 100.0,
                 "status": "completed", "events": []},
                {"id": "s2", "parent_id": "s1", "name": "l1",
                 "started_at": "2026-01-01T00:00:00+00:00", "duration_ms": 80.0,
                 "status": "completed", "events": []},
                {"id": "s3", "parent_id": "s2", "name": "l2",
                 "started_at": "2026-01-01T00:00:00+00:00", "duration_ms": 50.0,
                 "status": "completed", "events": []},
            ],
        }
        result = _compute_waterfall(trace)
        assert result[0]["depth"] == 0
        assert result[1]["depth"] == 1
        assert result[2]["depth"] == 2


class TestDashboardPages:
    def test_dashboard_empty(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "AgentWatch" in r.text

    def test_dashboard_with_data(self, seeded_client):
        r = seeded_client.get("/")
        assert r.status_code == 200
        assert "test-agent" in r.text

    def test_traces_page(self, seeded_client):
        r = seeded_client.get("/traces")
        assert r.status_code == 200
        assert "task-0" in r.text

    def test_traces_filter_by_status(self, seeded_client):
        r = seeded_client.get("/traces?status=failed")
        assert r.status_code == 200
        assert "failing-task" in r.text

    def test_trace_detail_page(self, seeded_client):
        r = seeded_client.get("/traces/trace-0")
        assert r.status_code == 200
        assert "task-0" in r.text

    def test_trace_detail_with_spans(self, seeded_client):
        """Test trace detail page renders waterfall for traces with spans."""
        from agentwatch.core import get_agent
        from agentwatch.models import Trace, Span, TraceStatus
        agent = get_agent()

        # Create a trace with spans
        trace = Trace(id="trace-with-spans", agent_name="test-agent", name="pipeline")
        root = Span(id="span-root", trace_id=trace.id, name="root")
        child = Span(id="span-child", trace_id=trace.id, parent_id="span-root", name="step-1")
        child.finish()
        root.finish()
        trace.root_span = root
        trace.finish()

        agent.storage.save_trace(trace)
        agent.storage.save_span(child)

        r = seeded_client.get("/traces/trace-with-spans")
        assert r.status_code == 200
        assert "Waterfall" in r.text
        assert "step-1" in r.text

    def test_trace_not_found(self, seeded_client):
        r = seeded_client.get("/traces/nonexistent")
        assert r.status_code == 200
        assert "not found" in r.text.lower()

    def test_health_page(self, seeded_client):
        r = seeded_client.get("/health")
        assert r.status_code == 200
        assert "database" in r.text

    def test_logs_page(self, seeded_client):
        r = seeded_client.get("/logs")
        assert r.status_code == 200
        assert "Log message" in r.text

    def test_logs_filter_by_level(self, seeded_client):
        r = seeded_client.get("/logs?level=error")
        assert r.status_code == 200
        assert "Something broke" in r.text

    def test_costs_page(self, seeded_client):
        r = seeded_client.get("/costs")
        assert r.status_code == 200
        assert "Cost" in r.text

    def test_patterns_page(self, seeded_client):
        r = seeded_client.get("/patterns")
        assert r.status_code == 200
        assert "Pattern" in r.text or "Trend" in r.text


class TestAPIEndpoints:
    def test_api_stats(self, seeded_client):
        r = seeded_client.get("/api/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total_traces"] == 4
        assert "test-agent" in data["agents"]

    def test_api_traces(self, seeded_client):
        r = seeded_client.get("/api/traces")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 4

    def test_api_traces_with_limit(self, seeded_client):
        r = seeded_client.get("/api/traces?limit=2")
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_api_trace_detail(self, seeded_client):
        r = seeded_client.get("/api/traces/trace-0")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "task-0"

    def test_api_trace_not_found(self, seeded_client):
        r = seeded_client.get("/api/traces/nonexistent")
        assert r.status_code == 200
        assert r.json() == {}

    def test_api_logs(self, seeded_client):
        r = seeded_client.get("/api/logs")
        assert r.status_code == 200
        assert len(r.json()) == 6

    def test_api_logs_level_filter(self, seeded_client):
        r = seeded_client.get("/api/logs?level=error")
        assert r.status_code == 200
        data = r.json()
        assert all(l["level"] == "error" for l in data)

    def test_api_health(self, seeded_client):
        r = seeded_client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["name"] == "database"

    def test_api_costs(self, seeded_client):
        r = seeded_client.get("/api/costs")
        assert r.status_code == 200
        data = r.json()
        assert data["total_cost_usd"] > 0
        assert data["record_count"] == 1

    def test_api_cost_usage(self, seeded_client):
        r = seeded_client.get("/api/costs/usage")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["model"] == "gpt-4o"

    def test_api_patterns(self, seeded_client):
        r = seeded_client.get("/api/patterns")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_api_trends(self, seeded_client):
        r = seeded_client.get("/api/trends")
        assert r.status_code == 200
        data = r.json()
        assert "direction" in data
        assert "error_rate" in data

    def test_prometheus_metrics(self, seeded_client):
        """Prometheus /metrics endpoint should return valid metrics."""
        r = seeded_client.get("/metrics")
        assert r.status_code == 200
        text = r.text
        assert "agentwatch_traces_total" in text
        assert "# TYPE" in text
        assert "# HELP" in text

    def test_prometheus_metrics_empty(self, client):
        """Metrics endpoint should work with no data."""
        r = client.get("/metrics")
        assert r.status_code == 200

    def test_agents_page(self, seeded_client):
        """Agents comparison page should render."""
        r = seeded_client.get("/agents")
        assert r.status_code == 200
        assert "Agent Comparison" in r.text

    def test_agents_page_empty(self, client):
        """Agents page should work with no data."""
        r = client.get("/agents")
        assert r.status_code == 200


class TestServerAuth:
    """Tests for dashboard authentication."""

    @pytest.fixture()
    def auth_client(self):
        """Create a test client with authentication enabled."""
        _reset()
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        app = create_app(db_path=path, auth_token="test-secret-token")
        client = TestClient(app)
        init("test-agent", db_path=path)
        yield client
        _reset()
        os.unlink(path)

    def test_unauthenticated_dashboard_redirects(self, auth_client):
        """Unauthenticated requests to dashboard redirect to login."""
        r = auth_client.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["location"]

    def test_unauthenticated_api_returns_401(self, auth_client):
        """Unauthenticated API requests return 401."""
        r = auth_client.get("/api/stats")
        assert r.status_code == 401
        assert "Authentication required" in r.text

    def test_login_page_renders(self, auth_client):
        """Login page renders without auth."""
        r = auth_client.get("/login")
        assert r.status_code == 200
        assert "Access Token" in r.text

    def test_login_with_valid_token(self, auth_client):
        """Posting valid token sets cookie and redirects."""
        r = auth_client.post(
            "/login",
            data={"token": "test-secret-token", "next": "/"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "agentwatch_token" in r.headers.get("set-cookie", "")

    def test_login_with_invalid_token(self, auth_client):
        """Posting invalid token shows error."""
        r = auth_client.post(
            "/login",
            data={"token": "wrong-token", "next": "/"},
        )
        assert r.status_code == 401
        assert "Invalid token" in r.text

    def test_authenticated_via_cookie(self, auth_client):
        """Requests with valid cookie can access dashboard."""
        r = auth_client.get(
            "/",
            cookies={"agentwatch_token": "test-secret-token"},
        )
        assert r.status_code == 200
        assert "AgentWatch" in r.text

    def test_authenticated_via_header(self, auth_client):
        """Requests with Bearer token can access API."""
        r = auth_client.get(
            "/api/stats",
            headers={"Authorization": "Bearer test-secret-token"},
        )
        assert r.status_code == 200

    def test_authenticated_via_query_param(self, auth_client):
        """Requests with token query param can access API."""
        r = auth_client.get("/api/stats?token=test-secret-token")
        assert r.status_code == 200

    def test_authenticated_via_custom_header(self, auth_client):
        """Requests with X-AgentWatch-Token header can access API."""
        r = auth_client.get(
            "/api/stats",
            headers={"X-AgentWatch-Token": "test-secret-token"},
        )
        assert r.status_code == 200

    def test_metrics_excluded_from_auth(self, auth_client):
        """Metrics endpoint is accessible without auth."""
        r = auth_client.get("/metrics")
        assert r.status_code == 200

    def test_health_excluded_from_auth(self, auth_client):
        """Health endpoint is accessible without auth."""
        r = auth_client.get("/health")
        # /health might 404 if not defined, but shouldn't 401/302
        assert r.status_code != 401
        assert r.status_code != 302

    def test_logout_clears_cookie(self, auth_client):
        """Logout endpoint clears the auth cookie."""
        r = auth_client.get("/logout", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["location"]

    def test_no_auth_client_works_normally(self, client):
        """When no auth token is set, everything works without authentication."""
        r = client.get("/")
        assert r.status_code == 200
