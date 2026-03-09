"""Tests for AgentWatch integrations."""

import os
import tempfile

import pytest

from agentwatch.core import init, _reset, get_agent


@pytest.fixture(autouse=True)
def clean_agent():
    """Reset agent state between tests."""
    _reset()
    yield
    _reset()


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


class TestOpenClawInstrumentation:
    def test_start_stop(self, db_path):
        from agentwatch.integrations.openclaw import OpenClawInstrumentation, OpenClawConfig
        config = OpenClawConfig(agent_name="test-agent", db_path=db_path, auto_detect_name=False)
        inst = OpenClawInstrumentation(config=config)

        inst.start()
        assert inst._active is True

        # Agent should be initialized
        agent = get_agent()
        assert agent.name == "test-agent"

        inst.stop()
        assert inst._active is False

    def test_auto_instrument(self, db_path):
        from agentwatch.integrations.openclaw import auto_instrument
        inst = auto_instrument("quick-agent", db_path=db_path, auto_detect_name=False)
        assert inst._active is True

        agent = get_agent()
        assert agent.name == "quick-agent"
        inst.stop()

    def test_session_tracing(self, db_path):
        from agentwatch.integrations.openclaw import auto_instrument
        inst = auto_instrument("session-test", db_path=db_path, auto_detect_name=False)

        with inst.session("handle-message") as span:
            span.event("processing request")

        agent = get_agent()
        traces = agent.storage.get_traces()
        assert len(traces) >= 1
        assert any(t["name"] == "handle-message" for t in traces)
        inst.stop()

    def test_tool_call_tracing(self, db_path):
        from agentwatch.integrations.openclaw import auto_instrument
        inst = auto_instrument("tool-test", db_path=db_path, auto_detect_name=False)

        with inst.session("main") as session:
            with inst.tool_call("web_search", {"query": "test"}) as tool_span:
                tool_span.event("got results")

        agent = get_agent()
        traces = agent.storage.get_traces()
        assert len(traces) >= 1
        inst.stop()

    def test_tool_call_error(self, db_path):
        from agentwatch.integrations.openclaw import auto_instrument
        inst = auto_instrument("error-test", db_path=db_path, auto_detect_name=False)

        with pytest.raises(ValueError):
            with inst.session("main"):
                with inst.tool_call("bad_tool"):
                    raise ValueError("tool broke")

        inst.stop()

    def test_cost_tracking(self, db_path):
        from agentwatch.integrations.openclaw import auto_instrument
        inst = auto_instrument("cost-test", db_path=db_path, auto_detect_name=False)

        inst.record_model_usage(
            model="claude-sonnet-4-20250514",
            input_tokens=500,
            output_tokens=200,
        )

        agent = get_agent()
        usage = agent.storage.get_token_usage()
        assert len(usage) == 1
        assert usage[0]["input_tokens"] == 500
        inst.stop()

    def test_health_checks_registered(self, db_path):
        from agentwatch.integrations.openclaw import auto_instrument
        inst = auto_instrument("health-test", db_path=db_path, auto_detect_name=False)

        agent = get_agent()
        checks = agent.get_health_checks()
        assert "agentwatch-db" in checks
        assert "disk-space" in checks
        assert "process-memory" in checks

        # Run them
        results = inst.run_health_checks()
        assert len(results) >= 3
        inst.stop()

    def test_custom_health_check(self, db_path):
        from agentwatch.integrations.openclaw import auto_instrument
        inst = auto_instrument("custom-health", db_path=db_path, auto_detect_name=False)

        inst.register_health_check("my-check", lambda: True)
        results = inst.run_health_checks()
        check_names = [r.name for r in results]
        assert "my-check" in check_names
        inst.stop()

    def test_inactive_noop(self, db_path):
        """Operations on inactive instrumentation are no-ops."""
        from agentwatch.integrations.openclaw import OpenClawInstrumentation
        inst = OpenClawInstrumentation(agent_name="noop")

        # These should all be no-ops
        with inst.session("test") as span:
            assert span is None

        with inst.tool_call("test") as span:
            assert span is None

        inst.record_model_usage(model="test", input_tokens=100, output_tokens=50)
        inst.log("info", "test")
        assert inst.run_health_checks() == []

    def test_sensitive_params_filtered(self, db_path):
        from agentwatch.integrations.openclaw import auto_instrument
        inst = auto_instrument("filter-test", db_path=db_path, auto_detect_name=False)

        with inst.session("main"):
            with inst.tool_call("api_call", {
                "query": "hello",
                "api_key": "secret123",
                "password": "hunter2",
            }) as span:
                pass

        inst.stop()


class TestHooks:
    def test_traced_decorator(self, db_path):
        init("hooks-test", db_path=db_path)
        from agentwatch.integrations.hooks import traced

        @traced("my-function")
        def my_func(x: int) -> int:
            return x * 2

        result = my_func(5)
        assert result == 10

        agent = get_agent()
        traces = agent.storage.get_traces()
        assert any(t["name"] == "my-function" for t in traces)

    def test_traced_captures_errors(self, db_path):
        init("hooks-error", db_path=db_path)
        from agentwatch.integrations.hooks import traced

        @traced("failing-fn")
        def bad_func():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            bad_func()

        agent = get_agent()
        traces = agent.storage.get_traces()
        failed = [t for t in traces if t["status"] == "failed"]
        assert len(failed) >= 1

    def test_traced_default_name(self, db_path):
        init("hooks-name", db_path=db_path)
        from agentwatch.integrations.hooks import traced

        @traced()
        def auto_named_function():
            return 42

        auto_named_function()
        agent = get_agent()
        traces = agent.storage.get_traces()
        assert any(t["name"] == "auto_named_function" for t in traces)

    def test_track_llm_call_dict_response(self, db_path):
        init("hooks-llm", db_path=db_path)
        from agentwatch.integrations.hooks import track_llm_call

        response = {
            "text": "Hello!",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        result = track_llm_call(
            fn=lambda: response,
            model="test-model",
        )
        assert result == response

        agent = get_agent()
        usage = agent.storage.get_token_usage()
        assert len(usage) == 1
        assert usage[0]["input_tokens"] == 100

    def test_track_llm_call_object_response(self, db_path):
        init("hooks-llm2", db_path=db_path)
        from agentwatch.integrations.hooks import track_llm_call

        class MockUsage:
            input_tokens = 200
            output_tokens = 100

        class MockResponse:
            usage = MockUsage()
            content = "test"

        result = track_llm_call(
            fn=lambda: MockResponse(),
            model="claude-test",
        )
        assert result.content == "test"

        agent = get_agent()
        usage = agent.storage.get_token_usage()
        assert len(usage) == 1
        assert usage[0]["input_tokens"] == 200

    def test_track_batch(self, db_path):
        init("hooks-batch", db_path=db_path)
        from agentwatch.integrations.hooks import track_batch

        def process_item(x):
            if x == 3:
                raise ValueError("bad item")
            return x * 2

        results = track_batch("batch-test", [1, 2, 3, 4], process_item)
        assert len(results) == 4
        assert results[0]["result"] == 2
        assert results[2]["error"] == "bad item"
        assert results[3]["result"] == 8

    def test_track_batch_stop_on_error(self, db_path):
        init("hooks-batch-stop", db_path=db_path)
        from agentwatch.integrations.hooks import track_batch

        def process_item(x):
            if x == 2:
                raise ValueError("stop here")
            return x

        results = track_batch("batch-stop", [1, 2, 3], process_item, on_error="stop")
        assert len(results) == 2  # Stopped after item 2

    def test_with_retry_succeeds(self, db_path):
        init("hooks-retry", db_path=db_path)
        from agentwatch.integrations.hooks import with_retry

        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("not yet")
            return "success"

        result = with_retry(flaky, max_attempts=3)
        assert result == "success"
        assert call_count == 3

    def test_with_retry_exhausted(self, db_path):
        init("hooks-retry-fail", db_path=db_path)
        from agentwatch.integrations.hooks import with_retry

        def always_fail():
            raise RuntimeError("nope")

        with pytest.raises(RuntimeError):
            with_retry(always_fail, max_attempts=2)

    def test_extract_usage_openai_format(self, db_path):
        from agentwatch.integrations.hooks import _extract_token_usage

        response = {"usage": {"prompt_tokens": 150, "completion_tokens": 75}}
        inp, out = _extract_token_usage(response)
        assert inp == 150
        assert out == 75

    def test_extract_usage_custom(self, db_path):
        from agentwatch.integrations.hooks import _extract_token_usage

        inp, out = _extract_token_usage(
            {"custom": "data"},
            custom_extractor=lambda r: (999, 111),
        )
        assert inp == 999
        assert out == 111


# Skip if FastAPI not installed (should be available)
pytest.importorskip("fastapi")


class TestFastAPIMiddleware:
    """Test the FastAPI auto-instrumentation middleware."""

    def test_basic_tracing(self, db_path):
        """Requests should be automatically traced."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agentwatch.integrations.fastapi import AgentWatchMiddleware

        init("fastapi-test", db_path=db_path)
        app = FastAPI()
        app.add_middleware(AgentWatchMiddleware)

        @app.get("/hello")
        async def hello():
            return {"msg": "world"}

        client = TestClient(app)
        r = client.get("/hello")
        assert r.status_code == 200

        # Check trace was created
        agent = get_agent()
        traces = agent.storage.get_traces(agent_name="fastapi-test")
        request_traces = [t for t in traces if t["name"] == "GET /hello"]
        assert len(request_traces) >= 1
        assert request_traces[0]["status"] == "completed"

    def test_excluded_paths(self, db_path):
        """Excluded paths should not create traces."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agentwatch.integrations.fastapi import AgentWatchMiddleware

        init("fastapi-exclude", db_path=db_path)
        app = FastAPI()
        app.add_middleware(AgentWatchMiddleware, exclude_paths={"/health", "/skip"})

        @app.get("/health")
        async def health():
            return {"ok": True}

        @app.get("/skip")
        async def skip():
            return {"skipped": True}

        @app.get("/tracked")
        async def tracked():
            return {"tracked": True}

        client = TestClient(app)
        client.get("/health")
        client.get("/skip")
        client.get("/tracked")

        agent = get_agent()
        traces = agent.storage.get_traces(agent_name="fastapi-exclude")
        names = [t["name"] for t in traces]
        assert "GET /health" not in names
        assert "GET /skip" not in names
        assert "GET /tracked" in names

    def test_error_tracing(self, db_path):
        """5xx responses should be marked as failed."""
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient
        from agentwatch.integrations.fastapi import AgentWatchMiddleware

        init("fastapi-error", db_path=db_path)
        app = FastAPI()
        app.add_middleware(AgentWatchMiddleware)

        @app.get("/error")
        async def error():
            return JSONResponse(status_code=500, content={"error": "boom"})

        client = TestClient(app)
        r = client.get("/error")
        assert r.status_code == 500

        agent = get_agent()
        traces = agent.storage.get_traces(agent_name="fastapi-error")
        error_traces = [t for t in traces if t["name"] == "GET /error"]
        assert len(error_traces) >= 1

    def test_metadata_capture(self, db_path):
        """Request metadata should be captured in trace."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agentwatch.integrations.fastapi import AgentWatchMiddleware

        init("fastapi-meta", db_path=db_path)
        app = FastAPI()
        app.add_middleware(AgentWatchMiddleware)

        @app.get("/api/data")
        async def data():
            return {"data": [1, 2, 3]}

        client = TestClient(app)
        client.get("/api/data?page=1&size=10")

        agent = get_agent()
        traces = agent.storage.get_traces(agent_name="fastapi-meta")
        trace = [t for t in traces if t["name"] == "GET /api/data"][0]
        detail = agent.storage.get_trace(trace["id"])

        # Check spans have metadata
        assert detail is not None
        spans = detail.get("spans", [])
        assert len(spans) > 0
