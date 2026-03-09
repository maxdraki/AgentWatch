"""
Async tracing module for AgentWatch.

Provides async-compatible context managers and decorators for tracing
agent workflows in asyncio-based applications. Uses contextvars for
proper async context propagation instead of thread-locals.

Usage::

    # Async context manager
    async with agentwatch.async_trace("fetch-data") as span:
        span.event("fetching from API")
        data = await client.get("/data")

    # Async decorator
    @agentwatch.async_trace("process")
    async def process_item(item):
        ...

    # Mixed with sync code (contextvars propagate correctly)
    async with agentwatch.async_trace("pipeline"):
        async with agentwatch.async_trace("step-1"):
            await do_work()
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable

from agentwatch.models import Span, SpanEvent, Trace, TraceStatus
from agentwatch.tracing import TracingSpan


# Context variable for async span stack (proper async context propagation)
_async_span_stack: contextvars.ContextVar[list[Span]] = contextvars.ContextVar(
    "agentwatch_async_span_stack",
    default=[],
)


def _get_current_async_span() -> Span | None:
    """Get the current span from async context."""
    stack = _async_span_stack.get()
    return stack[-1] if stack else None


def _push_async_span(span: Span) -> None:
    """Push a span onto the async context stack."""
    stack = _async_span_stack.get()
    # Create a new list to avoid mutating the parent context's list
    _async_span_stack.set([*stack, span])


def _pop_async_span() -> Span | None:
    """Pop a span from the async context stack."""
    stack = _async_span_stack.get()
    if not stack:
        return None
    span = stack[-1]
    _async_span_stack.set(stack[:-1])
    return span


class AsyncTracingSpan(TracingSpan):
    """
    Async-aware wrapper around Span.

    Extends TracingSpan with async-specific context management.
    Uses contextvars instead of thread-locals for proper async
    context propagation.
    """

    def _finish(self, error: str | None = None) -> None:
        """Finish the span and persist (async-aware context pop)."""
        from agentwatch.core import get_agent

        if error:
            self._span.finish(status=TraceStatus.FAILED, error=error)
        else:
            self._span.finish(status=TraceStatus.COMPLETED)

        _pop_async_span()

        # If this is the root span, finish the trace too
        if self._trace.root_span and self._trace.root_span.id == self._span.id:
            self._trace.finish()

        if self._auto_save:
            try:
                agent = get_agent()
                agent.storage.save_trace(self._trace)
            except RuntimeError:
                pass  # Agent not initialised


@asynccontextmanager
async def _async_trace_context(
    name: str,
    parent: TracingSpan | None = None,
    metadata: dict[str, Any] | None = None,
) -> AsyncGenerator[AsyncTracingSpan, None]:
    """Async context manager implementation of async_trace()."""
    from agentwatch.core import get_agent

    try:
        agent = get_agent()
        agent_name = agent.name
    except RuntimeError:
        agent_name = "unknown"

    # Determine parent span (check async context first, then thread-local)
    parent_span = _get_current_async_span()
    if parent:
        parent_span_id = parent._span.id
        trace_obj = parent._trace
    elif parent_span:
        parent_span_id = parent_span.id
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

    _push_async_span(span)
    tracing_span = AsyncTracingSpan(span, trace_obj)

    try:
        yield tracing_span
        tracing_span._finish()
    except Exception as exc:
        tracing_span._finish(error=str(exc))
        raise


def async_trace(
    name: str | Callable | None = None,
    parent: TracingSpan | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """
    Trace an async block of work.

    Can be used as an async context manager or decorator:

        # Async context manager
        async with agentwatch.async_trace("my-task") as span:
            span.event("doing stuff")
            await do_work()

        # Decorator
        @agentwatch.async_trace("my-task")
        async def do_stuff():
            ...

        # Bare decorator (uses function name)
        @agentwatch.async_trace
        async def do_stuff():
            ...

    Args:
        name: Human-readable name for this trace/span.
        parent: Optional parent TracingSpan for nesting.
        metadata: Optional metadata dict.
    """
    # Bare decorator: @async_trace (without parens)
    if callable(name):
        fn = name
        fn_name = getattr(fn, "__name__", "unknown")

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async with _async_trace_context(fn_name):
                return await fn(*args, **kwargs)

        return wrapper

    # Decorator factory or context manager: @async_trace("name") or async with async_trace("name")
    trace_name = name or "unnamed"

    class _AsyncTraceDual:
        """Dual async context-manager / decorator."""

        async def __aenter__(self):
            self._ctx = _async_trace_context(trace_name, parent=parent, metadata=metadata)
            return await self._ctx.__aenter__()

        async def __aexit__(self, *exc_info):
            return await self._ctx.__aexit__(*exc_info)

        def __call__(self, fn: Callable) -> Callable:
            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                async with _async_trace_context(trace_name, parent=parent, metadata=metadata):
                    return await fn(*args, **kwargs)
            return wrapper

    return _AsyncTraceDual()


def get_current_async_span() -> Span | None:
    """
    Get the currently active async span.

    Useful for adding events or metadata from deep in the call stack
    without passing the span object around.

    Returns:
        The current Span or None if no span is active.
    """
    return _get_current_async_span()
