"""Tests for the CrewAI callback integration."""

import os
import tempfile
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

import agentwatch
from agentwatch.integrations.crewai import AgentWatchCrewCallbacks, instrument_crew


@pytest.fixture
def storage():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    from agentwatch.storage import Storage
    s = Storage(db_path=path)
    yield s
    s.close()
    os.unlink(path)


@pytest.fixture
def agent(storage):
    agentwatch.init("test-agent", db_path=storage.db_path)
    yield
    from agentwatch.core import _reset
    from agentwatch.tracing import _context
    if hasattr(_context, "span_stack"):
        _context.span_stack.clear()
    _reset()


class TestCrewCallbacks:
    """Test the AgentWatchCrewCallbacks handler."""

    def test_on_step_basic(self, agent):
        callbacks = AgentWatchCrewCallbacks()

        step = MagicMock()
        step.__class__.__name__ = "AgentAction"
        step.tool = "web_search"
        step.tool_input = "search for cats"
        step.log = None
        step.text = None
        step.result = None
        step.return_values = None

        callbacks.on_step(step)
        assert callbacks.stats["steps"] == 1

    def test_on_step_with_crew_trace(self, agent):
        callbacks = AgentWatchCrewCallbacks()

        with callbacks.trace_crew("test-crew"):
            step = MagicMock()
            step.__class__.__name__ = "AgentAction"
            step.tool = "calculator"
            step.tool_input = "2+2"
            step.log = None
            step.text = None
            step.result = None
            step.return_values = None

            callbacks.on_step(step)

        assert callbacks.stats["steps"] == 1

    def test_on_step_agent_finish(self, agent):
        callbacks = AgentWatchCrewCallbacks()

        step = MagicMock()
        step.__class__.__name__ = "AgentFinish"
        step.tool = None
        step.tool_input = None
        step.log = None
        step.text = "Final answer: 42"
        step.result = None
        step.return_values = {"output": "42"}

        callbacks.on_step(step)
        assert callbacks.stats["steps"] == 1

    def test_on_step_capture_output(self, agent):
        callbacks = AgentWatchCrewCallbacks(capture_output=True)

        step = MagicMock()
        step.__class__.__name__ = "AgentAction"
        step.tool = "web_search"
        step.tool_input = "query for info"
        step.log = "Thought: I need to search..."
        step.text = None
        step.result = "Found some results"
        step.return_values = None

        callbacks.on_step(step)
        assert callbacks.stats["steps"] == 1

    def test_on_task_complete(self, agent):
        callbacks = AgentWatchCrewCallbacks()

        task_output = MagicMock()
        task_output.description = "Research the topic"
        task_output.raw = "Here is the research..."
        task_output.raw_output = "Here is the research..."
        task_output.agent = "Researcher"
        task_output.name = None

        callbacks.on_task_complete(task_output)
        assert callbacks.stats["tasks"] == 1

    def test_on_task_complete_with_name(self, agent):
        callbacks = AgentWatchCrewCallbacks()

        task_output = MagicMock()
        task_output.description = "A long description here"
        task_output.raw = "output"
        task_output.name = "research-task"
        task_output.agent = "Agent1"
        task_output.raw_output = "output"

        callbacks.on_task_complete(task_output)
        assert callbacks.stats["tasks"] == 1

    def test_on_task_complete_capture_output(self, agent):
        callbacks = AgentWatchCrewCallbacks(capture_output=True)

        task_output = MagicMock()
        task_output.description = "Write a poem"
        task_output.raw = "Roses are red..."
        task_output.raw_output = "Roses are red..."
        task_output.agent = "Poet"
        task_output.name = None

        callbacks.on_task_complete(task_output)
        assert callbacks.stats["tasks"] == 1


class TestTraceCrew:
    """Test the trace_crew context manager."""

    def test_trace_crew_basic(self, agent):
        callbacks = AgentWatchCrewCallbacks()

        with callbacks.trace_crew("my-crew"):
            pass

        # Should have cleared internal state
        assert callbacks._active_crew_span is None
        assert callbacks._active_crew_ctx is None

    def test_trace_crew_with_steps_and_tasks(self, agent):
        callbacks = AgentWatchCrewCallbacks()

        with callbacks.trace_crew("research-crew"):
            step = MagicMock()
            step.__class__.__name__ = "AgentAction"
            step.tool = "search"
            step.tool_input = "query"
            step.log = step.text = step.result = step.return_values = None
            callbacks.on_step(step)
            callbacks.on_step(step)

            task_output = MagicMock()
            task_output.description = "Task 1"
            task_output.raw = task_output.raw_output = "result"
            task_output.agent = "Agent"
            task_output.name = None
            callbacks.on_task_complete(task_output)

        assert callbacks.stats["steps"] == 2
        assert callbacks.stats["tasks"] == 1

    def test_trace_crew_error(self, agent):
        callbacks = AgentWatchCrewCallbacks()

        with pytest.raises(ValueError, match="crew error"):
            with callbacks.trace_crew("failing-crew"):
                raise ValueError("crew error")

        assert callbacks._active_crew_span is None

    def test_trace_crew_with_metadata(self, agent):
        callbacks = AgentWatchCrewCallbacks()

        with callbacks.trace_crew("meta-crew", metadata={"version": "1.0"}):
            pass

    def test_custom_prefix(self, agent):
        callbacks = AgentWatchCrewCallbacks(trace_name_prefix="myapp")

        with callbacks.trace_crew("test"):
            step = MagicMock()
            step.__class__.__name__ = "Step"
            step.tool = step.tool_input = step.log = None
            step.text = step.result = step.return_values = None
            callbacks.on_step(step)

        assert callbacks.stats["steps"] == 1


class TestInstrumentCrew:
    """Test the instrument_crew helper function."""

    def test_instrument_crew(self, agent):
        crew = MagicMock()
        crew.name = "test-crew"
        crew.kickoff.return_value = "result"

        instrumented = instrument_crew(crew)

        assert instrumented is crew
        assert hasattr(crew, "_agentwatch_callbacks")
        assert crew.step_callback is not None
        assert crew.task_callback is not None

    def test_instrument_crew_custom_name(self, agent):
        crew = MagicMock()
        crew.kickoff.return_value = "result"

        instrument_crew(crew, crew_name="custom-name")
        assert hasattr(crew, "_agentwatch_callbacks")

    def test_instrument_crew_kickoff_traced(self, agent):
        crew = MagicMock()
        crew.name = "traced-crew"
        original_result = {"output": "done"}
        crew.kickoff.return_value = original_result

        instrument_crew(crew)

        # Call the wrapped kickoff
        result = crew.kickoff()
        assert result == original_result

    def test_instrument_crew_stats_accessible(self, agent):
        crew = MagicMock()
        crew.name = "stats-crew"
        crew.kickoff.return_value = "ok"

        instrument_crew(crew)
        stats = crew._agentwatch_callbacks.stats
        assert stats["steps"] == 0
        assert stats["tasks"] == 0


class TestMultipleSteps:
    """Test realistic multi-step scenarios."""

    def test_full_crew_simulation(self, agent):
        """Simulate a complete crew run with multiple agents and tasks."""
        callbacks = AgentWatchCrewCallbacks()

        with callbacks.trace_crew("research-and-write"):
            # Agent 1: Research step
            step1 = MagicMock()
            step1.__class__.__name__ = "AgentAction"
            step1.tool = "web_search"
            step1.tool_input = "AI trends 2025"
            step1.log = step1.text = step1.result = step1.return_values = None
            callbacks.on_step(step1)

            # Agent 1: Finish
            step2 = MagicMock()
            step2.__class__.__name__ = "AgentFinish"
            step2.tool = step2.tool_input = step2.log = step2.result = None
            step2.text = "Found relevant information"
            step2.return_values = None
            callbacks.on_step(step2)

            # Task 1 complete
            task1 = MagicMock()
            task1.description = "Research AI trends"
            task1.raw = task1.raw_output = "Research findings..."
            task1.agent = "Researcher"
            task1.name = None
            callbacks.on_task_complete(task1)

            # Agent 2: Write step
            step3 = MagicMock()
            step3.__class__.__name__ = "AgentAction"
            step3.tool = "text_editor"
            step3.tool_input = "Write article"
            step3.log = step3.text = step3.result = step3.return_values = None
            callbacks.on_step(step3)

            # Task 2 complete
            task2 = MagicMock()
            task2.description = "Write article"
            task2.raw = task2.raw_output = "The AI landscape..."
            task2.agent = "Writer"
            task2.name = None
            callbacks.on_task_complete(task2)

        assert callbacks.stats["steps"] == 3
        assert callbacks.stats["tasks"] == 2
        assert callbacks.stats["errors"] == 0
