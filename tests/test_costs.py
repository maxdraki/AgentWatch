"""Tests for the cost tracking module."""

import os
import tempfile

import pytest

from agentwatch.core import init, _reset
from agentwatch.costs import (
    TokenUsage,
    CostSummary,
    estimate_cost,
    record,
    summary,
    set_pricing,
    _resolve_model,
    PROVIDER_PRICING,
)


@pytest.fixture(autouse=True)
def clean_state():
    """Reset global state and use a temp DB for each test."""
    _reset()
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init("test-agent", db_path=path)
    yield path
    _reset()
    os.unlink(path)


class TestEstimateCost:
    def test_known_model(self):
        cost = estimate_cost("gpt-4o", input_tokens=1_000_000, output_tokens=0)
        assert cost == pytest.approx(2.5, rel=0.01)

    def test_known_model_output(self):
        cost = estimate_cost("gpt-4o", input_tokens=0, output_tokens=1_000_000)
        assert cost == pytest.approx(10.0, rel=0.01)

    def test_mixed_tokens(self):
        cost = estimate_cost("gpt-4o", input_tokens=500_000, output_tokens=500_000)
        expected = 1.25 + 5.0  # half input + half output
        assert cost == pytest.approx(expected, rel=0.01)

    def test_unknown_model_returns_zero(self):
        cost = estimate_cost("totally-unknown-model-xyz", input_tokens=1000, output_tokens=500)
        assert cost == 0.0

    def test_alias_resolution(self):
        cost_alias = estimate_cost("sonnet", input_tokens=1000, output_tokens=500)
        resolved = _resolve_model("sonnet")
        cost_direct = estimate_cost(resolved, input_tokens=1000, output_tokens=500)
        assert cost_alias == cost_direct
        assert cost_alias > 0

    def test_small_usage(self):
        # 100 tokens should be very cheap
        cost = estimate_cost("gpt-4o-mini", input_tokens=100, output_tokens=50)
        assert cost > 0
        assert cost < 0.001


class TestResolveModel:
    def test_exact_match(self):
        assert _resolve_model("gpt-4o") == "gpt-4o"

    def test_alias(self):
        assert _resolve_model("sonnet") == "claude-sonnet-4-20250514"

    def test_case_insensitive_alias(self):
        assert _resolve_model("SONNET") == "claude-sonnet-4-20250514"

    def test_unknown_passthrough(self):
        assert _resolve_model("my-custom-model") == "my-custom-model"


class TestRecord:
    def test_basic_record(self, clean_state):
        usage = record(model="gpt-4o", input_tokens=1000, output_tokens=500)
        assert isinstance(usage, TokenUsage)
        assert usage.model == "gpt-4o"
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500
        assert usage.total_tokens == 1500
        assert usage.estimated_cost_usd > 0
        assert usage.agent_name == "test-agent"

    def test_explicit_cost(self, clean_state):
        usage = record(model="custom-model", input_tokens=100, output_tokens=50, cost_usd=0.42)
        assert usage.estimated_cost_usd == 0.42

    def test_with_metadata(self, clean_state):
        usage = record(
            model="gpt-4o",
            input_tokens=100,
            output_tokens=50,
            metadata={"purpose": "classification"},
        )
        assert usage.metadata["purpose"] == "classification"

    def test_record_persists(self, clean_state):
        record(model="gpt-4o", input_tokens=1000, output_tokens=500)
        record(model="gpt-4o", input_tokens=2000, output_tokens=1000)

        from agentwatch.core import get_agent
        agent = get_agent()
        records = agent.storage.get_token_usage()
        assert len(records) == 2

    def test_trace_context_linking(self, clean_state):
        import agentwatch
        with agentwatch.trace("test-task"):
            usage = record(model="gpt-4o", input_tokens=100, output_tokens=50)
        assert usage.trace_id is not None
        assert usage.span_id is not None


class TestSummary:
    def test_empty_summary(self, clean_state):
        s = summary()
        assert isinstance(s, CostSummary)
        assert s.total_cost_usd == 0
        assert s.record_count == 0

    def test_summary_after_records(self, clean_state):
        record(model="gpt-4o", input_tokens=1000, output_tokens=500)
        record(model="gpt-4o", input_tokens=2000, output_tokens=1000)

        s = summary()
        assert s.record_count == 2
        assert s.total_tokens == 4500
        assert s.total_cost_usd > 0

    def test_summary_by_agent(self, clean_state):
        record(model="gpt-4o", input_tokens=1000, output_tokens=500)
        s = summary(agent_name="test-agent")
        assert s.record_count == 1


class TestSetPricing:
    def test_custom_pricing(self):
        set_pricing("my-model", 5.0, 15.0)
        assert "my-model" in PROVIDER_PRICING
        cost = estimate_cost("my-model", input_tokens=1_000_000, output_tokens=1_000_000)
        assert cost == pytest.approx(20.0, rel=0.01)

    def test_override_existing(self):
        original = estimate_cost("gpt-4o", input_tokens=1_000_000, output_tokens=0)
        set_pricing("gpt-4o", 99.0, 99.0)
        new = estimate_cost("gpt-4o", input_tokens=1_000_000, output_tokens=0)
        assert new == pytest.approx(99.0, rel=0.01)
        assert new != original
        # Restore
        set_pricing("gpt-4o", 2.5, 10.0)


class TestTokenUsageModel:
    def test_to_dict(self):
        usage = TokenUsage(
            agent_name="test",
            model="gpt-4o",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            estimated_cost_usd=0.001,
        )
        d = usage.to_dict()
        assert d["model"] == "gpt-4o"
        assert d["total_tokens"] == 150
        assert "timestamp" in d
