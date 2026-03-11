"""
CrewAI integration for AgentWatch.

Automatically traces CrewAI agent steps, task completions, and crew
executions. Works with CrewAI's ``step_callback`` and ``task_callback``
parameters.

Usage::

    from crewai import Agent, Task, Crew
    from agentwatch.integrations.crewai import AgentWatchCrewCallbacks

    callbacks = AgentWatchCrewCallbacks()

    # Create crew with AgentWatch callbacks
    crew = Crew(
        agents=[...],
        tasks=[...],
        step_callback=callbacks.on_step,
        task_callback=callbacks.on_task_complete,
    )

    # Wrap the kickoff in a trace
    with callbacks.trace_crew("my-crew"):
        result = crew.kickoff()

Or with the helper::

    from agentwatch.integrations.crewai import instrument_crew

    crew = Crew(agents=[...], tasks=[...])
    crew = instrument_crew(crew)  # Adds callbacks automatically
    result = crew.kickoff()       # Automatically traced

The integration captures:
    - Each agent step (tool use, thinking) as a span event
    - Task completions with output and timing
    - Overall crew execution as a trace
    - Agent roles and task descriptions as metadata

Requires ``crewai`` to be installed. AgentWatch has no dependency on
CrewAI — this is an optional integration.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Generator

import agentwatch
from agentwatch.tracing import trace as _trace


class AgentWatchCrewCallbacks:
    """
    Callback handler for CrewAI crews.

    Provides ``on_step`` and ``on_task_complete`` methods compatible with
    CrewAI's ``step_callback`` and ``task_callback`` parameters.

    Args:
        trace_name_prefix: Prefix for trace/span names (default: "crewai").
        capture_output: Whether to include task outputs in metadata
            (default: False for privacy).
    """

    def __init__(
        self,
        trace_name_prefix: str = "crewai",
        capture_output: bool = False,
    ):
        self.trace_name_prefix = trace_name_prefix
        self.capture_output = capture_output

        # Stats
        self._step_count = 0
        self._task_count = 0
        self._error_count = 0
        self._active_crew_ctx: Any = None
        self._active_crew_span: Any = None
        self._task_spans: dict[str, Any] = {}

    @property
    def stats(self) -> dict[str, Any]:
        """Return usage statistics."""
        return {
            "steps": self._step_count,
            "tasks": self._task_count,
            "errors": self._error_count,
        }

    @contextmanager
    def trace_crew(
        self,
        crew_name: str = "crew",
        metadata: dict[str, Any] | None = None,
    ) -> Generator[Any, None, None]:
        """
        Context manager to trace an entire crew execution.

        Usage::

            callbacks = AgentWatchCrewCallbacks()
            with callbacks.trace_crew("research-crew"):
                crew.kickoff()

        Args:
            crew_name: Human-readable name for the crew trace.
            metadata: Optional metadata to attach.
        """
        name = f"{self.trace_name_prefix}.crew.{crew_name}"
        ctx = _trace(name, metadata=metadata)
        span = ctx.__enter__()
        self._active_crew_ctx = ctx
        self._active_crew_span = span

        try:
            yield span
            ctx.__exit__(None, None, None)
        except Exception as exc:
            ctx.__exit__(type(exc), exc, exc.__traceback__)
            raise
        finally:
            self._active_crew_ctx = None
            self._active_crew_span = None

    def on_step(self, step_output: Any) -> None:
        """
        Called by CrewAI on each agent step.

        This is the ``step_callback`` parameter for ``Crew()``.
        Receives various step output types from CrewAI:
        - ``AgentAction`` — tool invocations
        - ``AgentFinish`` — when agent finishes reasoning
        - Other step types depending on CrewAI version

        Args:
            step_output: The step output from CrewAI (varies by type).
        """
        self._step_count += 1

        # Extract what we can from the step output
        step_type = type(step_output).__name__

        # Try to get tool info (AgentAction has tool/tool_input)
        tool = getattr(step_output, "tool", None)
        tool_input = getattr(step_output, "tool_input", None)
        log = getattr(step_output, "log", None)
        text = getattr(step_output, "text", None)
        result = getattr(step_output, "result", None)
        return_values = getattr(step_output, "return_values", None)

        # Build event description
        parts = [f"step:{step_type}"]
        if tool:
            parts.append(f"tool={tool}")
        if text:
            parts.append(f"text={str(text)[:100]}")

        event_msg = " | ".join(parts)

        # Log to the active crew span if we have one
        if self._active_crew_span:
            self._active_crew_span.event(event_msg)

        # Also emit a structured log
        metadata: dict[str, Any] = {"step_type": step_type}
        if tool:
            metadata["tool"] = str(tool)
        if self.capture_output:
            if tool_input:
                metadata["tool_input"] = str(tool_input)[:500]
            if log:
                metadata["log"] = str(log)[:500]
            if result:
                metadata["result"] = str(result)[:500]
            if return_values:
                metadata["return_values"] = str(return_values)[:500]

        agentwatch.log(
            "debug",
            f"[{self.trace_name_prefix}] {event_msg}",
            metadata=metadata,
        )

    def on_task_complete(self, task_output: Any) -> None:
        """
        Called by CrewAI when a task finishes.

        This is the ``task_callback`` parameter for ``Crew()``.

        Args:
            task_output: The TaskOutput from CrewAI.
        """
        self._task_count += 1

        # Extract task info
        description = getattr(task_output, "description", "")
        raw_output = getattr(task_output, "raw", "") or getattr(task_output, "raw_output", "")
        agent_role = getattr(task_output, "agent", "")
        name = getattr(task_output, "name", None)

        task_name = name or (str(description)[:50] + "…" if len(str(description)) > 50 else str(description))

        event_msg = f"task_complete: {task_name}"
        if agent_role:
            event_msg += f" (agent: {agent_role})"

        # Log to crew span
        if self._active_crew_span:
            self._active_crew_span.event(event_msg)

        # Structured log
        metadata: dict[str, Any] = {
            "task_name": str(task_name),
            "agent": str(agent_role),
        }
        if self.capture_output and raw_output:
            metadata["output_preview"] = str(raw_output)[:500]

        agentwatch.log(
            "info",
            f"[{self.trace_name_prefix}] {event_msg}",
            metadata=metadata,
        )


def instrument_crew(
    crew: Any,
    crew_name: str | None = None,
    capture_output: bool = False,
    trace_name_prefix: str = "crewai",
) -> Any:
    """
    Instrument a CrewAI Crew instance with AgentWatch callbacks.

    Adds step and task callbacks, and wraps ``kickoff()`` in a trace.

    Args:
        crew: The CrewAI Crew instance.
        crew_name: Name for the trace (default: uses crew's name attribute or "crew").
        capture_output: Whether to capture task outputs.
        trace_name_prefix: Prefix for trace names.

    Returns:
        The same crew instance (modified in place).

    Usage::

        crew = Crew(agents=[...], tasks=[...])
        crew = instrument_crew(crew, crew_name="research-crew")
        result = crew.kickoff()  # Automatically traced
    """
    callbacks = AgentWatchCrewCallbacks(
        trace_name_prefix=trace_name_prefix,
        capture_output=capture_output,
    )

    resolved_name = crew_name or getattr(crew, "name", None) or "crew"

    # Set callbacks on the crew
    crew.step_callback = callbacks.on_step
    crew.task_callback = callbacks.on_task_complete

    # Wrap kickoff to auto-trace
    original_kickoff = crew.kickoff

    def traced_kickoff(*args: Any, **kwargs: Any) -> Any:
        with callbacks.trace_crew(resolved_name):
            return original_kickoff(*args, **kwargs)

    crew.kickoff = traced_kickoff
    crew._agentwatch_callbacks = callbacks  # Keep reference for stats access

    return crew
