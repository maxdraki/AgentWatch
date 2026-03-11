"""Tests for the remote AgentWatch client."""

from __future__ import annotations

import json
import threading
from unittest.mock import patch, MagicMock

import pytest

from agentwatch.client import (
    AgentWatchClient,
    ClientSpan,
    ClientTrace,
)


class TestClientSpan:
    def test_event(self):
        span = ClientSpan(name="test")
        span.event("hello")
        assert len(span.events) == 1
        assert span.events[0].message == "hello"

    def test_event_with_metadata(self):
        span = ClientSpan(name="test")
        span.event("found items", {"count": 5})
        assert span.events[0].metadata == {"count": 5}

    def test_set_metadata(self):
        span = ClientSpan(name="test")
        span.set_metadata("key", "value")
        assert span.metadata["key"] == "value"

    def test_set_error(self):
        span = ClientSpan(name="test")
        span.set_error("boom")
        assert span.error == "boom"
        assert span.status == "failed"

    def test_finish(self):
        span = ClientSpan(name="test")
        span._finish()
        assert span.status == "completed"
        assert span.ended_at is not None
        assert span.duration_ms is not None
        assert span.duration_ms >= 0

    def test_finish_preserves_failed(self):
        span = ClientSpan(name="test")
        span.set_error("fail")
        span._finish()
        assert span.status == "failed"

    def test_to_dict(self):
        span = ClientSpan(name="test", trace_id="t1")
        span.event("event1")
        d = span.to_dict()
        assert d["name"] == "test"
        assert d["trace_id"] == "t1"
        assert len(d["events"]) == 1


class TestClientTrace:
    def test_trace_context_manager(self):
        sent_data = []
        client = AgentWatchClient("http://fake:8470", agent_name="test-agent")
        client._send = lambda cat, data: sent_data.append((cat, data))

        with client.trace("my-task") as t:
            t.event("started")
            t.set_metadata("key", "value")

        assert len(sent_data) == 1
        cat, data = sent_data[0]
        assert cat == "traces"
        assert data["name"] == "my-task"
        assert data["agent_name"] == "test-agent"
        assert data["status"] == "completed"
        assert data["duration_ms"] is not None
        assert len(data["spans"]) == 1

    def test_trace_with_error(self):
        sent_data = []
        client = AgentWatchClient("http://fake:8470")
        client._send = lambda cat, data: sent_data.append((cat, data))

        try:
            with client.trace("failing-task") as t:
                raise ValueError("test error")
        except ValueError:
            pass

        assert len(sent_data) == 1
        data = sent_data[0][1]
        assert data["status"] == "failed"

    def test_trace_with_children(self):
        sent_data = []
        client = AgentWatchClient("http://fake:8470")
        client._send = lambda cat, data: sent_data.append((cat, data))

        with client.trace("parent") as t:
            t.event("root event")
            with t.child("child-1") as c1:
                c1.event("child event")
            with t.child("child-2") as c2:
                c2.set_metadata("result", "ok")

        data = sent_data[0][1]
        assert len(data["spans"]) == 3  # root + 2 children
        child_names = [s["name"] for s in data["spans"]]
        assert "parent" in child_names
        assert "child-1" in child_names
        assert "child-2" in child_names

    def test_child_error_propagation(self):
        sent_data = []
        client = AgentWatchClient("http://fake:8470")
        client._send = lambda cat, data: sent_data.append((cat, data))

        try:
            with client.trace("parent") as t:
                with t.child("failing-child") as c:
                    raise RuntimeError("child failed")
        except RuntimeError:
            pass

        data = sent_data[0][1]
        # Find the failing child span
        child_span = [s for s in data["spans"] if s["name"] == "failing-child"][0]
        assert child_span["status"] == "failed"
        assert child_span["error"] == "child failed"


class TestAgentWatchClient:
    def test_log(self):
        sent_data = []
        client = AgentWatchClient("http://fake:8470", agent_name="test")
        client._send = lambda cat, data: sent_data.append((cat, data))

        client.log("info", "Agent started", {"version": "1.0"})

        assert len(sent_data) == 1
        cat, data = sent_data[0]
        assert cat == "logs"
        assert data["level"] == "info"
        assert data["message"] == "Agent started"
        assert data["agent_name"] == "test"
        assert data["metadata"]["version"] == "1.0"

    def test_health(self):
        sent_data = []
        client = AgentWatchClient("http://fake:8470", agent_name="test")
        client._send = lambda cat, data: sent_data.append((cat, data))

        client.health("database", status="ok", message="Connected", duration_ms=5.2)

        cat, data = sent_data[0]
        assert cat == "health"
        assert data["name"] == "database"
        assert data["status"] == "ok"
        assert data["duration_ms"] == 5.2

    def test_cost(self):
        sent_data = []
        client = AgentWatchClient("http://fake:8470", agent_name="test")
        client._send = lambda cat, data: sent_data.append((cat, data))

        client.cost(model="gpt-4o", input_tokens=500, output_tokens=200)

        cat, data = sent_data[0]
        assert cat == "costs"
        assert data["model"] == "gpt-4o"
        assert data["input_tokens"] == 500
        assert data["output_tokens"] == 200

    def test_buffered_mode(self):
        http_calls = []

        client = AgentWatchClient("http://fake:8470", agent_name="test", buffer_size=3)
        client._http_post = lambda path, data: http_calls.append((path, data))

        client.log("info", "msg1")
        client.log("info", "msg2")
        assert len(http_calls) == 0  # Not flushed yet

        client.log("info", "msg3")  # Buffer full — should auto-flush
        assert len(http_calls) == 1
        assert http_calls[0][0] == "/api/v1/ingest/batch"

    def test_manual_flush(self):
        http_calls = []

        client = AgentWatchClient("http://fake:8470", agent_name="test", buffer_size=100)
        client._http_post = lambda path, data: http_calls.append((path, data))

        client.log("info", "msg1")
        client.log("info", "msg2")
        count = client.flush()

        assert count == 2
        assert len(http_calls) == 1

    def test_flush_empty(self):
        client = AgentWatchClient("http://fake:8470", buffer_size=100)
        assert client.flush() == 0

    def test_stats(self):
        client = AgentWatchClient("http://fake:8470", buffer_size=100)
        client._http_post = lambda *a: None

        client.log("info", "msg1")
        client.log("info", "msg2")

        stats = client.stats
        assert stats["buffered"] == 2
        assert stats["total_sent"] == 0

        client.flush()
        stats = client.stats
        assert stats["buffered"] == 0
        assert stats["total_sent"] == 2

    def test_repr(self):
        client = AgentWatchClient("http://localhost:8470", agent_name="my-agent")
        r = repr(client)
        assert "localhost:8470" in r
        assert "my-agent" in r

    def test_immediate_mode(self):
        http_calls = []

        client = AgentWatchClient("http://fake:8470", agent_name="test", buffer_size=0)
        client._http_post = lambda path, data: http_calls.append((path, data))

        client.log("info", "immediate")
        assert len(http_calls) == 1
        assert http_calls[0][0] == "/api/v1/ingest/logs"


class TestClientIntegration:
    """End-to-end test: client → server → storage."""

    def test_client_to_server(self, tmp_path):
        """Full round-trip: client sends data, server ingests, data is queryable."""
        from agentwatch.server.app import create_app
        from starlette.testclient import TestClient

        db_path = str(tmp_path / "integration.db")
        app = create_app(db_path=db_path)

        with TestClient(app) as test_client:
            # Create a real client that posts to the test server
            client = AgentWatchClient(
                server_url="http://testserver",
                agent_name="integration-test",
            )

            # Override _http_post to use the test client
            def mock_post(path, data):
                resp = test_client.post(path, json=data)
                resp.raise_for_status()
                return resp.json()

            client._http_post = mock_post

            # Send data
            with client.trace("integration-task") as t:
                t.event("started")
                t.set_metadata("test", True)

            client.log("info", "Integration test log")
            client.health("test-check", status="ok", message="all good")
            client.cost(model="gpt-4o", input_tokens=100, output_tokens=50)

            # Verify via API
            traces = test_client.get("/api/traces?agent=integration-test").json()
            assert len(traces) >= 1
            assert any(t["name"] == "integration-task" for t in traces)

            logs = test_client.get("/api/logs?agent=integration-test").json()
            assert len(logs) >= 1

            health = test_client.get("/api/health?agent=integration-test").json()
            assert len(health) >= 1

            costs = test_client.get("/api/costs?agent=integration-test").json()
            assert costs["total_tokens"] > 0
