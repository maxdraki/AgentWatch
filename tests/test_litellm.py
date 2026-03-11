"""Tests for the LiteLLM callback integration."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentwatch.integrations.litellm import (
    AgentWatchCallback,
    _compute_duration_ms,
    _extract_content,
    _extract_provider,
    _extract_usage,
    _short_model,
)


@pytest.fixture(autouse=True)
def init_agentwatch(tmp_path):
    """Ensure agentwatch is initialised for tests."""
    import agentwatch
    from agentwatch.core import _reset
    db_path = str(tmp_path / "test.db")
    agentwatch.init("test-agent", db_path=db_path)
    yield
    _reset()


class TestHelpers:
    def test_compute_duration_ms(self):
        start = datetime(2026, 3, 10, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 10, 1, 0, 5, tzinfo=timezone.utc)
        assert _compute_duration_ms(start, end) == 5000.0

    def test_compute_duration_ms_none(self):
        assert _compute_duration_ms(None, None) == 0.0

    def test_extract_usage_from_object(self):
        response = MagicMock()
        response.usage.prompt_tokens = 100
        response.usage.completion_tokens = 50
        response.usage.total_tokens = 150

        usage = _extract_usage(response)
        assert usage is not None
        assert usage["prompt_tokens"] == 100
        assert usage["completion_tokens"] == 50

    def test_extract_usage_from_dict(self):
        response = {
            "usage": {
                "prompt_tokens": 200,
                "completion_tokens": 100,
                "total_tokens": 300,
            }
        }
        usage = _extract_usage(response)
        assert usage["prompt_tokens"] == 200

    def test_extract_usage_none(self):
        assert _extract_usage("not a response") is None

    def test_extract_content_from_object(self):
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "Hello world"

        assert _extract_content(response) == "Hello world"

    def test_extract_content_from_dict(self):
        response = {
            "choices": [{"message": {"content": "Hello"}}]
        }
        assert _extract_content(response) == "Hello"

    def test_extract_content_empty(self):
        assert _extract_content({}) == ""

    def test_extract_provider(self):
        assert _extract_provider({"model": "anthropic/claude-3"}) == "anthropic"
        assert _extract_provider({"model": "gpt-4o"}) == "unknown"
        assert _extract_provider({
            "litellm_params": {"custom_llm_provider": "openai"},
            "model": "gpt-4o",
        }) == "openai"

    def test_short_model(self):
        assert _short_model("claude-sonnet-4-20250514") == "sonnet-4"
        assert _short_model("anthropic/claude-sonnet-4-20250514") == "sonnet-4"
        assert _short_model("gpt-4o") == "gpt-4o"
        assert _short_model("some-custom-model") == "some-custom-model"


class TestAgentWatchCallback:
    def test_success_handler(self):
        callback = AgentWatchCallback()

        response = MagicMock()
        response.usage.prompt_tokens = 500
        response.usage.completion_tokens = 200
        response.usage.total_tokens = 700
        response._hidden_params = {}

        start = datetime(2026, 3, 10, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 10, 1, 0, 2, tzinfo=timezone.utc)

        callback.log_success_event(
            kwargs={"model": "gpt-4o", "litellm_params": {}},
            response_obj=response,
            start_time=start,
            end_time=end,
        )

        assert callback._call_count == 1
        assert callback._error_count == 0
        assert callback._total_tokens == 700

    def test_failure_handler(self):
        callback = AgentWatchCallback()

        start = datetime(2026, 3, 10, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 10, 1, 0, 1, tzinfo=timezone.utc)

        callback.log_failure_event(
            kwargs={
                "model": "gpt-4o",
                "exception": ValueError("Rate limited"),
            },
            response_obj=None,
            start_time=start,
            end_time=end,
        )

        assert callback._call_count == 1
        assert callback._error_count == 1

    def test_stats(self):
        callback = AgentWatchCallback()
        stats = callback.stats
        assert stats["calls"] == 0
        assert stats["errors"] == 0
        assert stats["total_tokens"] == 0
        assert stats["total_cost_usd"] == 0.0

    def test_capture_messages(self):
        callback = AgentWatchCallback(capture_messages=True)

        response = MagicMock()
        response.usage.prompt_tokens = 100
        response.usage.completion_tokens = 50
        response.usage.total_tokens = 150
        response._hidden_params = {}
        response.choices = [MagicMock()]
        response.choices[0].message.content = "Hello from the LLM"

        start = datetime(2026, 3, 10, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 10, 1, 0, 1, tzinfo=timezone.utc)

        callback.log_success_event(
            kwargs={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Say hello"}],
                "litellm_params": {},
            },
            response_obj=response,
            start_time=start,
            end_time=end,
        )

        assert callback._call_count == 1

    def test_no_costs_when_disabled(self):
        callback = AgentWatchCallback(record_costs=False)

        response = MagicMock()
        response.usage.prompt_tokens = 100
        response.usage.completion_tokens = 50
        response.usage.total_tokens = 150
        response._hidden_params = {}

        start = datetime(2026, 3, 10, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 10, 1, 0, 1, tzinfo=timezone.utc)

        callback.log_success_event(
            kwargs={"model": "gpt-4o", "litellm_params": {}},
            response_obj=response,
            start_time=start,
            end_time=end,
        )

        # Should still track stats internally
        assert callback._call_count == 1

    def test_custom_prefix(self):
        callback = AgentWatchCallback(trace_name_prefix="ai-call")
        assert callback.trace_name_prefix == "ai-call"

    @pytest.mark.asyncio
    async def test_async_success_handler(self):
        callback = AgentWatchCallback()

        response = MagicMock()
        response.usage.prompt_tokens = 100
        response.usage.completion_tokens = 50
        response.usage.total_tokens = 150
        response._hidden_params = {}

        start = datetime(2026, 3, 10, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 10, 1, 0, 1, tzinfo=timezone.utc)

        await callback.async_log_success_event(
            kwargs={"model": "gpt-4o", "litellm_params": {}},
            response_obj=response,
            start_time=start,
            end_time=end,
        )

        assert callback._call_count == 1

    @pytest.mark.asyncio
    async def test_async_failure_handler(self):
        callback = AgentWatchCallback()

        start = datetime(2026, 3, 10, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 10, 1, 0, 1, tzinfo=timezone.utc)

        await callback.async_log_failure_event(
            kwargs={"model": "gpt-4o", "exception": RuntimeError("timeout")},
            response_obj=None,
            start_time=start,
            end_time=end,
        )

        assert callback._error_count == 1

    def test_litellm_cost_extraction(self):
        callback = AgentWatchCallback()

        response = MagicMock()
        response.usage.prompt_tokens = 100
        response.usage.completion_tokens = 50
        response.usage.total_tokens = 150
        response._hidden_params = {"response_cost": 0.0042}

        start = datetime(2026, 3, 10, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 10, 1, 0, 1, tzinfo=timezone.utc)

        callback.log_success_event(
            kwargs={"model": "gpt-4o", "litellm_params": {}},
            response_obj=response,
            start_time=start,
            end_time=end,
        )

        assert callback._total_cost == 0.0042
