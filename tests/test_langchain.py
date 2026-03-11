"""Tests for the LangChain callback integration."""

import os
import tempfile
import uuid
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

import agentwatch
from agentwatch.integrations.langchain import AgentWatchHandler


@pytest.fixture
def storage():
    """Create a temporary storage."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    from agentwatch.storage import Storage
    s = Storage(db_path=path)
    yield s
    s.close()
    os.unlink(path)


@pytest.fixture
def agent(storage):
    """Initialise an agent with test storage."""
    agentwatch.init("test-agent", db_path=storage.db_path)
    yield
    from agentwatch.core import _reset
    from agentwatch.tracing import _context
    # Clean up any leftover span stack from handler tests
    if hasattr(_context, "span_stack"):
        _context.span_stack.clear()
    _reset()


def _run_id():
    return uuid.uuid4()


class TestLLMCallbacks:
    """Test LLM start/end/error callbacks."""

    def test_llm_start_and_end(self, agent):
        handler = AgentWatchHandler()
        run_id = _run_id()

        # Simulate LLM start
        handler.on_llm_start(
            serialized={"kwargs": {"model_name": "gpt-4"}, "id": ["ChatOpenAI"]},
            prompts=["Hello!"],
            run_id=run_id,
        )

        assert str(run_id) in handler._active_runs

        # Simulate LLM end with token usage
        response = MagicMock()
        response.llm_output = {
            "token_usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            }
        }
        response.generations = []

        handler.on_llm_end(response=response, run_id=run_id)

        assert str(run_id) not in handler._active_runs
        assert handler.stats["calls"] == 1
        assert handler.stats["total_tokens"] == 30

    def test_chat_model_start_and_end(self, agent):
        handler = AgentWatchHandler()
        run_id = _run_id()

        handler.on_chat_model_start(
            serialized={"kwargs": {"model": "claude-sonnet-4-20250514"}, "id": ["ChatAnthropic"]},
            messages=[[MagicMock(), MagicMock()]],
            run_id=run_id,
            invocation_params={"model": "claude-sonnet-4-20250514"},
        )

        assert str(run_id) in handler._active_runs
        run_data = handler._active_runs[str(run_id)]
        assert run_data["model"] == "claude-sonnet-4-20250514"

        response = MagicMock()
        response.llm_output = {
            "token_usage": {"input_tokens": 100, "output_tokens": 50}
        }
        response.generations = []

        handler.on_llm_end(response=response, run_id=run_id)
        assert handler.stats["total_tokens"] == 150

    def test_llm_error(self, agent):
        handler = AgentWatchHandler()
        run_id = _run_id()

        handler.on_llm_start(
            serialized={"kwargs": {"model_name": "gpt-4"}, "id": ["ChatOpenAI"]},
            prompts=["fail"],
            run_id=run_id,
        )

        error = RuntimeError("API timeout")
        handler.on_llm_error(error=error, run_id=run_id)

        assert handler.stats["errors"] == 1
        assert str(run_id) not in handler._active_runs

    def test_llm_no_tokens(self, agent):
        """When LLM response has no token info, still completes cleanly."""
        handler = AgentWatchHandler()
        run_id = _run_id()

        handler.on_llm_start(
            serialized={"kwargs": {}, "id": ["SomeLLM"]},
            prompts=["hello"],
            run_id=run_id,
        )

        response = MagicMock()
        response.llm_output = None
        response.generations = []

        handler.on_llm_end(response=response, run_id=run_id)
        assert handler.stats["calls"] == 1
        assert handler.stats["total_tokens"] == 0

    def test_capture_io(self, agent):
        """Test that capture_io records prompts and outputs."""
        handler = AgentWatchHandler(capture_io=True)
        run_id = _run_id()

        handler.on_llm_start(
            serialized={"kwargs": {"model_name": "gpt-4"}, "id": ["ChatOpenAI"]},
            prompts=["What is 2+2?"],
            run_id=run_id,
        )

        run_data = handler._active_runs[str(run_id)]
        assert run_data["span"]._span.metadata.get("prompts") == ["What is 2+2?"]

        # Create response with generations
        gen = MagicMock()
        gen.text = "4"
        response = MagicMock()
        response.llm_output = {"token_usage": {}}
        response.generations = [[gen]]

        handler.on_llm_end(response=response, run_id=run_id)


class TestChainCallbacks:
    """Test chain start/end/error callbacks."""

    def test_chain_start_and_end(self, agent):
        handler = AgentWatchHandler()
        run_id = _run_id()

        handler.on_chain_start(
            serialized={"name": "QAChain", "id": ["QAChain"]},
            inputs={"question": "What is AI?"},
            run_id=run_id,
        )

        assert str(run_id) in handler._active_runs

        handler.on_chain_end(
            outputs={"answer": "Artificial intelligence"},
            run_id=run_id,
        )

        assert str(run_id) not in handler._active_runs

    def test_chain_error(self, agent):
        handler = AgentWatchHandler()
        run_id = _run_id()

        handler.on_chain_start(
            serialized={"name": "FailChain", "id": ["FailChain"]},
            inputs={},
            run_id=run_id,
        )

        handler.on_chain_error(
            error=ValueError("bad input"),
            run_id=run_id,
        )

        assert handler.stats["errors"] == 1

    def test_chain_capture_io(self, agent):
        handler = AgentWatchHandler(capture_io=True)
        run_id = _run_id()

        handler.on_chain_start(
            serialized={"name": "Chain", "id": ["Chain"]},
            inputs={"a": 1, "b": 2},
            run_id=run_id,
        )

        run_data = handler._active_runs[str(run_id)]
        assert run_data["span"]._span.metadata.get("input_keys") == ["a", "b"]


class TestToolCallbacks:
    """Test tool start/end/error callbacks."""

    def test_tool_start_and_end(self, agent):
        handler = AgentWatchHandler()
        run_id = _run_id()

        handler.on_tool_start(
            serialized={"name": "calculator"},
            input_str="2 + 2",
            run_id=run_id,
        )

        assert str(run_id) in handler._active_runs

        handler.on_tool_end(
            output="4",
            run_id=run_id,
        )

        assert str(run_id) not in handler._active_runs

    def test_tool_error(self, agent):
        handler = AgentWatchHandler()
        run_id = _run_id()

        handler.on_tool_start(
            serialized={"name": "web_search"},
            input_str="query",
            run_id=run_id,
        )

        handler.on_tool_error(
            error=TimeoutError("search timed out"),
            run_id=run_id,
        )

        assert handler.stats["errors"] == 1

    def test_tool_capture_io(self, agent):
        handler = AgentWatchHandler(capture_io=True)
        run_id = _run_id()

        handler.on_tool_start(
            serialized={"name": "calculator"},
            input_str="compute 42 * 7",
            run_id=run_id,
        )

        run_data = handler._active_runs[str(run_id)]
        assert run_data["span"]._span.metadata.get("input") == "compute 42 * 7"

        handler.on_tool_end(output="294", run_id=run_id)


class TestRetrieverCallbacks:
    """Test retriever start/end/error callbacks."""

    def test_retriever_start_and_end(self, agent):
        handler = AgentWatchHandler()
        run_id = _run_id()

        handler.on_retriever_start(
            serialized={"name": "VectorStore", "id": ["VectorStore"]},
            query="What is machine learning?",
            run_id=run_id,
        )

        assert str(run_id) in handler._active_runs

        # Simulate returning documents
        docs = [MagicMock(), MagicMock(), MagicMock()]
        handler.on_retriever_end(documents=docs, run_id=run_id)

        assert str(run_id) not in handler._active_runs

    def test_retriever_error(self, agent):
        handler = AgentWatchHandler()
        run_id = _run_id()

        handler.on_retriever_start(
            serialized={"name": "FAISS"},
            query="search query",
            run_id=run_id,
        )

        handler.on_retriever_error(
            error=ConnectionError("vector store unavailable"),
            run_id=run_id,
        )

        assert handler.stats["errors"] == 1


class TestAgentCallbacks:
    """Test agent action/finish callbacks."""

    def test_agent_action(self, agent):
        handler = AgentWatchHandler()
        run_id = _run_id()

        # Start a chain (agent runs are chains)
        handler.on_chain_start(
            serialized={"name": "AgentExecutor"},
            inputs={"input": "search for cats"},
            run_id=run_id,
        )

        action = MagicMock()
        action.tool = "web_search"
        handler.on_agent_action(action=action, run_id=run_id)

    def test_agent_finish(self, agent):
        handler = AgentWatchHandler()
        run_id = _run_id()

        handler.on_chain_start(
            serialized={"name": "AgentExecutor"},
            inputs={},
            run_id=run_id,
        )

        finish = MagicMock()
        handler.on_agent_finish(finish=finish, run_id=run_id)

        # Chain should still be active (agent_finish doesn't end the chain)
        assert str(run_id) in handler._active_runs
        handler.on_chain_end(outputs={}, run_id=run_id)


class TestNestedCalls:
    """Test nested chain/LLM calls."""

    def test_chain_with_llm(self, agent):
        """Simulate a chain that calls an LLM internally."""
        handler = AgentWatchHandler()
        chain_id = _run_id()
        llm_id = _run_id()

        # Start chain
        handler.on_chain_start(
            serialized={"name": "QAChain"},
            inputs={"q": "test"},
            run_id=chain_id,
        )

        # LLM call inside chain
        handler.on_chat_model_start(
            serialized={"kwargs": {"model": "gpt-4o"}, "id": ["ChatOpenAI"]},
            messages=[[MagicMock()]],
            run_id=llm_id,
            parent_run_id=chain_id,
        )

        response = MagicMock()
        response.llm_output = {"token_usage": {"prompt_tokens": 50, "completion_tokens": 100}}
        response.generations = []
        handler.on_llm_end(response=response, run_id=llm_id)

        # End chain
        handler.on_chain_end(outputs={"answer": "test"}, run_id=chain_id)

        assert handler.stats["calls"] == 1
        assert handler.stats["total_tokens"] == 150
        assert len(handler._active_runs) == 0


class TestMiscBehaviour:
    """Test edge cases and miscellaneous behaviour."""

    def test_unknown_run_id_no_crash(self, agent):
        """Ending a run that was never started should not crash."""
        handler = AgentWatchHandler()
        fake_id = _run_id()

        handler.on_llm_end(response=MagicMock(), run_id=fake_id)
        handler.on_chain_end(outputs={}, run_id=fake_id)
        handler.on_tool_end(output="x", run_id=fake_id)
        handler.on_retriever_end(documents=[], run_id=fake_id)

        # Should not raise, stats should be clean
        assert handler.stats["calls"] == 0
        assert handler.stats["errors"] == 0

    def test_on_llm_new_token_noop(self, agent):
        """Token streaming callback should not crash."""
        handler = AgentWatchHandler()
        handler.on_llm_new_token(token="hello", run_id=_run_id())

    def test_on_text_noop(self, agent):
        handler = AgentWatchHandler()
        handler.on_text(text="something", run_id=_run_id())

    def test_custom_prefix(self, agent):
        handler = AgentWatchHandler(trace_name_prefix="myapp")
        run_id = _run_id()

        handler.on_tool_start(
            serialized={"name": "calc"},
            input_str="1+1",
            run_id=run_id,
        )

        run_data = handler._active_runs[str(run_id)]
        # The span name should use the custom prefix
        # (we can't easily check the span name directly, but we can
        # verify it was created)
        assert run_data["span"] is not None
        handler.on_tool_end(output="2", run_id=run_id)

    def test_stats_accumulate(self, agent):
        handler = AgentWatchHandler()

        for i in range(3):
            run_id = _run_id()
            handler.on_llm_start(
                serialized={"kwargs": {"model_name": "gpt-4"}, "id": ["ChatOpenAI"]},
                prompts=["hi"],
                run_id=run_id,
            )
            response = MagicMock()
            response.llm_output = {
                "token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
            }
            response.generations = []
            handler.on_llm_end(response=response, run_id=run_id)

        assert handler.stats["calls"] == 3
        assert handler.stats["total_tokens"] == 45

    def test_handler_flags(self):
        """Verify LangChain-expected flags are set."""
        handler = AgentWatchHandler()
        assert handler.ignore_llm is False
        assert handler.ignore_chain is False
        assert handler.ignore_agent is False
        assert handler.ignore_retriever is False
        assert handler.raise_error is False
