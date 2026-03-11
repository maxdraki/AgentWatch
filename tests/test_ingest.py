"""Tests for the HTTP ingestion API."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

import pytest

from agentwatch.ingest import (
    ingest_batch,
    ingest_cost,
    ingest_health,
    ingest_log,
    ingest_trace,
)
from agentwatch.storage import Storage


@pytest.fixture
def storage(tmp_path):
    db_path = str(tmp_path / "test.db")
    return Storage(db_path=db_path)


class TestIngestTrace:
    def test_basic_trace(self, storage):
        trace_id = ingest_trace({
            "name": "test-task",
            "agent_name": "test-agent",
            "status": "completed",
            "started_at": "2026-03-10T01:00:00+00:00",
            "ended_at": "2026-03-10T01:00:05+00:00",
            "duration_ms": 5000,
        }, storage)

        trace = storage.get_trace(trace_id)
        assert trace is not None
        assert trace["name"] == "test-task"
        assert trace["agent_name"] == "test-agent"
        assert trace["status"] == "completed"
        assert trace["duration_ms"] == 5000

    def test_trace_with_custom_id(self, storage):
        trace_id = ingest_trace({
            "id": "custom-id-123",
            "name": "task",
            "agent_name": "agent",
        }, storage)
        assert trace_id == "custom-id-123"
        trace = storage.get_trace("custom-id-123")
        assert trace is not None

    def test_trace_with_spans(self, storage):
        trace_id = ingest_trace({
            "name": "pipeline",
            "agent_name": "agent",
            "status": "completed",
            "started_at": "2026-03-10T01:00:00+00:00",
            "ended_at": "2026-03-10T01:00:10+00:00",
            "duration_ms": 10000,
            "spans": [
                {
                    "name": "fetch",
                    "status": "completed",
                    "started_at": "2026-03-10T01:00:00+00:00",
                    "ended_at": "2026-03-10T01:00:03+00:00",
                    "duration_ms": 3000,
                },
                {
                    "name": "process",
                    "parent_id": "will-be-set",
                    "status": "completed",
                    "started_at": "2026-03-10T01:00:03+00:00",
                    "ended_at": "2026-03-10T01:00:10+00:00",
                    "duration_ms": 7000,
                },
            ],
        }, storage)

        trace = storage.get_trace(trace_id)
        assert trace is not None
        # Root span should exist
        assert len(trace["spans"]) >= 1

    def test_trace_with_span_events(self, storage):
        trace_id = ingest_trace({
            "name": "task-with-events",
            "agent_name": "agent",
            "spans": [
                {
                    "name": "main",
                    "status": "completed",
                    "events": [
                        {"message": "started processing"},
                        {"message": "found 5 items", "metadata": {"count": 5}},
                    ],
                },
            ],
        }, storage)

        trace = storage.get_trace(trace_id)
        root_span = trace["spans"][0]
        assert len(root_span["events"]) == 2
        assert root_span["events"][0]["message"] == "started processing"

    def test_trace_with_metadata(self, storage):
        trace_id = ingest_trace({
            "name": "meta-task",
            "agent_name": "agent",
            "metadata": {"env": "production", "version": "1.2.3"},
        }, storage)

        trace = storage.get_trace(trace_id)
        assert trace["metadata"]["env"] == "production"

    def test_trace_defaults(self, storage):
        """Traces with minimal data should use sensible defaults."""
        trace_id = ingest_trace({"name": "minimal"}, storage)
        trace = storage.get_trace(trace_id)
        assert trace["agent_name"] == "remote"
        assert trace["name"] == "minimal"

    def test_failed_trace(self, storage):
        trace_id = ingest_trace({
            "name": "failing",
            "agent_name": "agent",
            "status": "failed",
            "spans": [{
                "name": "main",
                "status": "failed",
                "error": "Connection timeout",
            }],
        }, storage)

        trace = storage.get_trace(trace_id)
        assert trace["status"] == "failed"
        assert trace["spans"][0]["error"] == "Connection timeout"


class TestIngestLog:
    def test_basic_log(self, storage):
        log_id = ingest_log({
            "agent_name": "agent",
            "level": "info",
            "message": "Agent started",
        }, storage)

        logs = storage.get_logs(limit=10)
        assert len(logs) == 1
        assert logs[0]["message"] == "Agent started"
        assert logs[0]["level"] == "info"

    def test_log_with_metadata(self, storage):
        ingest_log({
            "agent_name": "agent",
            "level": "error",
            "message": "Request failed",
            "metadata": {"status_code": 500, "url": "https://api.example.com"},
        }, storage)

        logs = storage.get_logs(limit=10)
        assert logs[0]["metadata"]["status_code"] == 500

    def test_log_linked_to_trace(self, storage):
        ingest_log({
            "agent_name": "agent",
            "level": "info",
            "message": "Processing in trace",
            "trace_id": "trace-123",
            "span_id": "span-456",
        }, storage)

        logs = storage.get_logs(limit=10)
        assert logs[0]["trace_id"] == "trace-123"

    def test_log_defaults(self, storage):
        ingest_log({"message": "bare log"}, storage)
        logs = storage.get_logs(limit=10)
        assert logs[0]["agent_name"] == "remote"
        assert logs[0]["level"] == "info"


class TestIngestHealth:
    def test_basic_health(self, storage):
        name = ingest_health({
            "name": "database",
            "agent_name": "agent",
            "status": "ok",
            "message": "Connected",
            "duration_ms": 12.5,
        }, storage)

        assert name == "database"
        health = storage.get_health_latest()
        assert len(health) == 1
        assert health[0]["name"] == "database"
        assert health[0]["status"] == "ok"

    def test_health_critical(self, storage):
        ingest_health({
            "name": "api",
            "agent_name": "agent",
            "status": "critical",
            "message": "API unreachable",
        }, storage)

        health = storage.get_health_latest()
        assert health[0]["status"] == "critical"

    def test_health_with_metadata(self, storage):
        ingest_health({
            "name": "memory",
            "agent_name": "agent",
            "status": "warn",
            "metadata": {"usage_pct": 85.2},
        }, storage)

        health = storage.get_health_latest()
        assert health[0]["metadata"]["usage_pct"] == 85.2


class TestIngestCost:
    def test_basic_cost(self, storage):
        cost_id = ingest_cost({
            "agent_name": "agent",
            "model": "claude-sonnet-4-20250514",
            "input_tokens": 1000,
            "output_tokens": 500,
        }, storage)

        usage = storage.get_token_usage(limit=10)
        assert len(usage) == 1
        assert usage[0]["model"] == "claude-sonnet-4-20250514"
        assert usage[0]["input_tokens"] == 1000
        assert usage[0]["output_tokens"] == 500
        # Should have auto-estimated cost
        assert usage[0]["estimated_cost_usd"] > 0

    def test_cost_with_explicit_amount(self, storage):
        ingest_cost({
            "agent_name": "agent",
            "model": "custom-model",
            "input_tokens": 100,
            "output_tokens": 50,
            "estimated_cost_usd": 0.42,
        }, storage)

        usage = storage.get_token_usage(limit=10)
        assert usage[0]["estimated_cost_usd"] == 0.42

    def test_cost_linked_to_trace(self, storage):
        ingest_cost({
            "agent_name": "agent",
            "model": "gpt-4o",
            "input_tokens": 500,
            "output_tokens": 200,
            "trace_id": "trace-abc",
        }, storage)

        usage = storage.get_token_usage(limit=10)
        assert usage[0]["trace_id"] == "trace-abc"


class TestIngestBatch:
    def test_mixed_batch(self, storage):
        counts = ingest_batch({
            "traces": [
                {"name": "task-1", "agent_name": "agent", "status": "completed"},
                {"name": "task-2", "agent_name": "agent", "status": "failed"},
            ],
            "logs": [
                {"agent_name": "agent", "level": "info", "message": "Log 1"},
                {"agent_name": "agent", "level": "error", "message": "Log 2"},
                {"agent_name": "agent", "level": "warn", "message": "Log 3"},
            ],
            "health": [
                {"name": "db", "agent_name": "agent", "status": "ok"},
            ],
            "costs": [
                {"agent_name": "agent", "model": "gpt-4o", "input_tokens": 100, "output_tokens": 50},
            ],
        }, storage)

        assert counts == {"traces": 2, "logs": 3, "health": 1, "costs": 1, "metrics": 0, "model_usage": 0, "cron_runs": 0}

        # Verify data actually persisted
        traces = storage.get_traces(limit=10)
        assert len(traces) == 2

        logs = storage.get_logs(limit=10)
        assert len(logs) == 3

        health = storage.get_health_latest()
        assert len(health) == 1

        usage = storage.get_token_usage(limit=10)
        assert len(usage) == 1

    def test_empty_batch(self, storage):
        counts = ingest_batch({}, storage)
        assert counts == {"traces": 0, "logs": 0, "health": 0, "costs": 0, "metrics": 0, "model_usage": 0, "cron_runs": 0}

    def test_partial_batch(self, storage):
        counts = ingest_batch({
            "logs": [
                {"agent_name": "a", "message": "hello"},
            ],
        }, storage)
        assert counts["logs"] == 1
        assert counts["traces"] == 0


class TestIngestServerEndpoints:
    """Test the ingestion endpoints via the FastAPI test client."""

    @pytest.fixture
    def client(self, tmp_path):
        from agentwatch.server.app import create_app
        from starlette.testclient import TestClient

        db_path = str(tmp_path / "server_test.db")
        app = create_app(db_path=db_path)
        return TestClient(app)

    def test_post_trace(self, client):
        resp = client.post("/api/v1/ingest/traces", json={
            "name": "remote-task",
            "agent_name": "remote-agent",
            "status": "completed",
            "duration_ms": 1500,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["ingested"] == 1
        assert len(data["ids"]) == 1

    def test_post_trace_array(self, client):
        resp = client.post("/api/v1/ingest/traces", json=[
            {"name": "task-1", "agent_name": "a"},
            {"name": "task-2", "agent_name": "a"},
        ])
        assert resp.json()["ingested"] == 2

    def test_post_logs(self, client):
        resp = client.post("/api/v1/ingest/logs", json={
            "agent_name": "remote",
            "level": "info",
            "message": "Hello from remote",
        })
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 1

    def test_post_health(self, client):
        resp = client.post("/api/v1/ingest/health", json={
            "name": "api",
            "agent_name": "remote",
            "status": "ok",
        })
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 1

    def test_post_costs(self, client):
        resp = client.post("/api/v1/ingest/costs", json={
            "agent_name": "remote",
            "model": "gpt-4o",
            "input_tokens": 1000,
            "output_tokens": 500,
        })
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 1

    def test_post_batch(self, client):
        resp = client.post("/api/v1/ingest/batch", json={
            "traces": [{"name": "t1", "agent_name": "a"}],
            "logs": [{"message": "log1", "agent_name": "a"}],
        })
        data = resp.json()
        assert data["status"] == "ok"
        assert data["ingested"] == 2
        assert data["counts"]["traces"] == 1
        assert data["counts"]["logs"] == 1

    def test_ingested_traces_visible_in_api(self, client):
        """Verify ingested data shows up in the read API."""
        # Ingest
        client.post("/api/v1/ingest/traces", json={
            "name": "visible-task",
            "agent_name": "remote-agent",
            "status": "completed",
            "duration_ms": 2500,
        })

        # Read back
        resp = client.get("/api/traces?agent=remote-agent")
        traces = resp.json()
        assert len(traces) >= 1
        assert any(t["name"] == "visible-task" for t in traces)

    def test_ingested_with_auth(self, tmp_path):
        """Verify ingestion respects auth token."""
        from agentwatch.server.app import create_app
        from starlette.testclient import TestClient

        db_path = str(tmp_path / "auth_test.db")
        app = create_app(db_path=db_path, auth_token="secret123")
        client = TestClient(app)

        # Without token — should fail
        resp = client.post("/api/v1/ingest/traces", json={
            "name": "task", "agent_name": "a",
        })
        assert resp.status_code == 401

        # With token — should work
        resp = client.post(
            "/api/v1/ingest/traces",
            json={"name": "task", "agent_name": "a"},
            headers={"Authorization": "Bearer secret123"},
        )
        assert resp.status_code == 200
