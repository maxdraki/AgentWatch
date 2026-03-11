"""
Tests for model usage storage, ingestion, and LiteLLM callback integration.
"""

from __future__ import annotations

import datetime

import pytest

from agentwatch.storage import Storage
from agentwatch.ingest import ingest_model_usage


@pytest.fixture
def storage(tmp_path):
    return Storage(db_path=str(tmp_path / "test.db"))


# ─── Storage ─────────────────────────────────────────────────────────────────


def test_record_model_usage_returns_id(storage):
    """record_model_usage stores a row and returns a 16-char hex ID."""
    record_id = storage.record_model_usage(
        model="claude-sonnet-4-20250514",
        prompt_tokens=100,
        completion_tokens=50,
        cost_usd=0.001,
        latency_ms=250.0,
        agent_name="test-agent",
    )
    assert record_id and len(record_id) == 16


def test_get_model_stats_empty(storage):
    """get_model_stats returns empty list when no data."""
    assert storage.get_model_stats(hours=24) == []


def test_get_model_stats_aggregation(storage):
    """get_model_stats aggregates requests, tokens, and cost per model."""
    for _ in range(3):
        storage.record_model_usage(
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.01,
            latency_ms=200.0,
            agent_name="test",
        )
    for _ in range(2):
        storage.record_model_usage(
            model="claude-sonnet",
            prompt_tokens=200,
            completion_tokens=100,
            cost_usd=0.02,
            agent_name="test",
        )

    stats = storage.get_model_stats(hours=24)
    assert len(stats) == 2

    gpt = next(s for s in stats if s["model"] == "gpt-4o")
    assert gpt["requests"] == 3
    assert gpt["prompt_tokens"] == 300
    assert gpt["completion_tokens"] == 150
    assert gpt["total_tokens"] == 450
    assert abs(gpt["total_cost_usd"] - 0.03) < 1e-9


def test_get_model_stats_sorted_by_cost(storage):
    """get_model_stats returns models sorted by total cost descending."""
    storage.record_model_usage("cheap-model", 10, 5, 0.001)
    storage.record_model_usage("expensive-model", 10, 5, 0.999)

    stats = storage.get_model_stats(hours=24)
    assert stats[0]["model"] == "expensive-model"
    assert stats[1]["model"] == "cheap-model"


def test_get_model_stats_percentile_latency(storage):
    """Percentile latencies (p50, p95) are computed correctly."""
    latencies = [100.0, 200.0, 300.0, 400.0, 500.0]
    for lat in latencies:
        storage.record_model_usage(
            model="test-model",
            prompt_tokens=10,
            completion_tokens=5,
            cost_usd=0.001,
            latency_ms=lat,
            agent_name="test",
        )

    stats = storage.get_model_stats(hours=24)
    assert len(stats) == 1
    row = stats[0]
    assert row["p50_latency_ms"] is not None
    assert row["p95_latency_ms"] is not None
    assert row["p50_latency_ms"] <= row["p95_latency_ms"]


def test_get_model_stats_no_latency(storage):
    """Records without latency don't break p50/p95 (returns None)."""
    storage.record_model_usage(
        model="no-latency-model",
        prompt_tokens=10,
        completion_tokens=5,
        cost_usd=0.001,
        latency_ms=None,
    )
    stats = storage.get_model_stats(hours=24)
    assert stats[0]["p50_latency_ms"] is None
    assert stats[0]["p95_latency_ms"] is None


# ─── Ingestion ────────────────────────────────────────────────────────────────


def test_ingest_model_usage_full(storage):
    """ingest_model_usage parses a full dict and stores the record."""
    record_id = ingest_model_usage(
        {
            "model": "gemini-2.5-flash",
            "prompt_tokens": 500,
            "completion_tokens": 200,
            "cost_usd": 0.005,
            "latency_ms": 800,
            "agent_name": "remote-agent",
        },
        storage,
    )
    assert record_id

    stats = storage.get_model_stats(hours=24)
    assert len(stats) == 1
    assert stats[0]["model"] == "gemini-2.5-flash"
    assert stats[0]["requests"] == 1


def test_ingest_model_usage_minimal(storage):
    """ingest_model_usage handles missing optional fields gracefully."""
    record_id = ingest_model_usage({"model": "gpt-4o"}, storage)
    assert record_id
    stats = storage.get_model_stats(hours=24)
    assert stats[0]["prompt_tokens"] == 0
    assert stats[0]["total_cost_usd"] == 0.0


# ─── LiteLLM callback ────────────────────────────────────────────────────────


def test_litellm_callback_records_model_usage(tmp_path, monkeypatch):
    """AgentWatchCallback records model usage in the model_usage table."""
    import agentwatch

    agentwatch.init("test-litellm", db_path=str(tmp_path / "test.db"))
    try:
        from agentwatch.integrations.litellm import AgentWatchCallback

        cb = AgentWatchCallback(record_costs=True)

        # Minimal mock of a LiteLLM response
        mock_response = type(
            "Response",
            (),
            {
                "usage": type(
                    "Usage",
                    (),
                    {
                        "prompt_tokens": 100,
                        "completion_tokens": 50,
                        "total_tokens": 150,
                    },
                )(),
                "_hidden_params": {"response_cost": 0.002},
                "choices": [],
            },
        )()

        now = datetime.datetime.now()
        cb.log_success_event(
            kwargs={"model": "gpt-4o", "litellm_params": {}, "messages": []},
            response_obj=mock_response,
            start_time=now,
            end_time=now,
        )

        from agentwatch.core import _agent

        assert _agent is not None
        stats = _agent.storage.get_model_stats(hours=1)
        assert len(stats) > 0
        assert stats[0]["requests"] == 1
        assert stats[0]["prompt_tokens"] == 100
    finally:
        agentwatch.shutdown()
