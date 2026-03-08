"""
Tracing module for AgentWatch.

Provides both context-manager and decorator APIs for tracing
agent workflows. Traces are trees of spans — each span represents
a unit of work with timing, events, and error capture.

Usage:
    # Context manager
    with agentwatch.trace("process-emails") as span:
        span.event("found 3 emails")
        # ... work ...

    # Decorator
    @agentwatch.trace("classify")
    def classify_email(email):
        ...

    # Nested spans
    with agentwatch.trace("pipeline") as parent:
        with agentwatch.trace("step-1", parent=parent) as child:
            ...
"""

from __future__ import annotations

import functools
import threading
from contextlib import contextmanager
from typing import Any, Callable, Generator

from agentwatch.models import Span, SpanEvent, Trace, TraceStatus


# Thread-local storage for the current span context
_context = threading.local()


def _get_current_span() -> Span | None:
    """Get the current span from thread-local context."""
    stack = getattr(_context, "span_stack", [])
    return stack[-1] if stack else None


def _push_span(span: Span) -> None:
    if not hasattr(_context, "span_stack"):
        _context.span_stack = []
    _context.span_stack.append(span)


def _pop_span() -> Span | None:
    stack = getattr(_context, "span_stack", [])
    return stack.pop() if stack else None


class TracingSpan:
    """
    A wrapper around Span that provides a nice API and auto-persists.

    Used as a context manager or decorator target. On exit, the span
    is finished and saved to storage.
    """

    def __init__(self, span: Span, trace: Trace, auto_save: bool = True):
        self._span = span
        self._trace = trace
        self._auto_save = auto_save

    @property
    def id(self) -> str:
        return self._span.id

    @property
    def trace_id(self) -> str:
        return self._trace.id

    @property
    def name(self) -> str:
        return self._span.name

    @property
    def status(self) -> TraceStatus:
        return self._span.status

    @property
    def duration_ms(self) -> float | None:
        return self._span.duration_ms

    def event(self, message: str, metadata: dict[str, Any] | None = None) -> SpanEvent:
        """Record an event within this span."""
        return self._span.event(message, metadata)

    def set_metadata(self, key: str, value: Any) -> None:
        """Set a metadata key on this span."""
        self._span.metadata[key] = value

    def set_error(self, error: str) -> None:
        """Mark this span as failed with an error message."""
        self._span.error = error
        self._span.status = TraceStatus.FAILED

    def _finish(self, error: str | None = None) -> None:
        """Finish the span and persist."""
        from agentwatch.core import get_agent

        if error:
            self._span.finish(status=TraceStatus.FAILED, error=error)
        else:
            self._span.finish(status=TraceStatus.COMPLETED)

        _pop_span()

        # If this is the root span, finish the trace too
        if self._trace.root_span and self._trace.root_span.id == self._span.id:
            self._trace.finish()

        if self._auto_save:
            try:
                agent = get_agent()
                agent.storage.save_trace(self._trace)
            except RuntimeError:
                pass  # Agent not initialised — skip persistence


@contextmanager
def _trace_context(
    name: str,
    parent: TracingSpan | None = None,
    metadata: dict[str, Any] | None = None,
) -> Generator[TracingSpan, None, None]:
    """Context manager implementation of trace()."""
    from agentwatch.core import get_agent

    try:
        agent = get_agent()
        agent_name = agent.name
    except RuntimeError:
        agent_name = "unknown"

    # Determine parent span
    parent_span = _get_current_span()
    if parent:
        parent_span_id = parent._span.id
        trace_obj = parent._trace
    elif parent_span:
        parent_span_id = parent_span.id
        # Find the trace for the parent span
        trace_obj = Trace(agent_name=agent_name, name=name)
    else:
        parent_span_id = None
        trace_obj = Trace(agent_name=agent_name, name=name)

    # Create span
    span = Span(
        trace_id=trace_obj.id,
        parent_id=parent_span_id,
        name=name,
        metadata=metadata or {},
    )

    # If no parent, this is the root span
    if not parent_span_id:
        trace_obj.root_span = span

    _push_span(span)
    tracing_span = TracingSpan(span, trace_obj)

    try:
        yield tracing_span
        tracing_span._finish()
    except Exception as exc:
        tracing_span._finish(error=str(exc))
        raise


def trace(
    name: str,
    parent: TracingSpan | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """
    Trace a block of work.

    Can be used as a context manager or decorator:

        # Context manager
        with agentwatch.trace("my-task") as span:
            span.event("doing stuff")

        # Decorator
        @agentwatch.trace("my-task")
        def do_stuff():
            ...

    Args:
        name: Human-readable name for this trace/span.
        parent: Optional parent TracingSpan for nesting.
        metadata: Optional metadata dict.
    """
    # If called with a callable as the first arg, it's being used as
    # a bare decorator: @agentwatch.trace  (without parens)
    if callable(name):
        fn = name
        fn_name = getattr(fn, "__name__", "unknown")

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with _trace_context(fn_name):
                return fn(*args, **kwargs)

        return wrapper

    # Otherwise it might be @agentwatch.trace("name") as a decorator factory
    # or used as a context manager
    ctx = _trace_context(name, parent=parent, metadata=metadata)

    # Make it work as both decorator and context manager
    class _TraceDual:
        """Dual context-manager / decorator."""

        def __enter__(self):
            return ctx.__enter__()

        def __exit__(self, *exc_info):
            return ctx.__exit__(*exc_info)

        def __call__(self, fn: Callable) -> Callable:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                with _trace_context(name, parent=parent, metadata=metadata):
                    return fn(*args, **kwargs)
            return wrapper

    return _TraceDual()
