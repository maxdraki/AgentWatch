"""
Generic hooks for auto-instrumenting common patterns.

Provides decorators and wrappers for:
- Function tracing with automatic error capture
- LLM call wrapping with cost tracking
- Retry tracking
- Batch operation tracking

Usage:
    from agentwatch.integrations.hooks import traced, track_llm_call

    @traced("classify-email")
    def classify(email):
        ...

    result = track_llm_call(
        fn=lambda: client.messages.create(...),
        model="claude-sonnet-4-20250514",
    )
"""

from __future__ import annotations

import functools
import time
from typing import Any, Callable, TypeVar

import agentwatch

T = TypeVar("T")


def traced(
    name: str | None = None,
    capture_args: bool = False,
    capture_result: bool = False,
) -> Callable:
    """
    Decorator that wraps a function in an AgentWatch trace.

    Args:
        name: Trace name. Defaults to function name.
        capture_args: If True, logs function arguments as metadata.
        capture_result: If True, logs return value as an event.

    Usage:
        @traced("process-email")
        def process(email_id: str):
            ...

        @traced(capture_args=True, capture_result=True)
        def classify(text: str) -> str:
            ...
    """
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        trace_name = name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            metadata: dict[str, Any] = {}
            if capture_args:
                # Capture args safely (convert to str, truncate)
                try:
                    arg_strs = [_safe_repr(a) for a in args]
                    kwarg_strs = {k: _safe_repr(v) for k, v in kwargs.items()}
                    metadata["args"] = arg_strs
                    metadata["kwargs"] = kwarg_strs
                except Exception:
                    pass

            with agentwatch.trace(trace_name, metadata=metadata) as span:
                result = fn(*args, **kwargs)
                if capture_result:
                    span.event(f"result: {_safe_repr(result)}")
                return result

        return wrapper
    return decorator


def track_llm_call(
    fn: Callable[..., T],
    model: str,
    extract_usage: Callable[[Any], tuple[int, int]] | None = None,
    metadata: dict[str, Any] | None = None,
    *args: Any,
    **kwargs: Any,
) -> T:
    """
    Execute an LLM call and automatically track costs.

    Wraps the call in a trace span and records token usage.

    Args:
        fn: The function to call (e.g., client.messages.create).
        model: Model name for cost tracking.
        extract_usage: Custom function to extract (input_tokens, output_tokens)
                      from the response. If None, tries common patterns.
        metadata: Additional metadata for cost record.
        *args, **kwargs: Passed to fn.

    Returns:
        The result of fn().

    Usage:
        # With Anthropic client
        result = track_llm_call(
            fn=lambda: client.messages.create(
                model="claude-sonnet-4-20250514",
                messages=[...],
            ),
            model="claude-sonnet-4-20250514",
        )

        # With custom usage extractor
        result = track_llm_call(
            fn=my_api_call,
            model="custom-model",
            extract_usage=lambda r: (r["prompt_tokens"], r["completion_tokens"]),
        )
    """
    with agentwatch.trace(f"llm:{model}") as span:
        span.set_metadata("model", model)

        result = fn(*args, **kwargs)

        # Try to extract token usage
        input_tokens, output_tokens = _extract_token_usage(result, extract_usage)

        if input_tokens > 0 or output_tokens > 0:
            agentwatch.costs.record(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                metadata=metadata or {},
            )
            span.event(f"tokens: {input_tokens} in, {output_tokens} out")

        return result


def track_batch(
    name: str,
    items: list[Any],
    process_fn: Callable[[Any], Any],
    on_error: str = "continue",
) -> list[dict[str, Any]]:
    """
    Process a batch of items with per-item tracing.

    Args:
        name: Name for the batch trace.
        items: Items to process.
        process_fn: Function to call for each item.
        on_error: "continue" to keep going, "stop" to abort on first error.

    Returns:
        List of {"item": item, "result": result, "error": error_or_none}.

    Usage:
        results = track_batch(
            "classify-emails",
            emails,
            classify_email,
        )
    """
    results = []

    with agentwatch.trace(name) as batch_span:
        batch_span.event(f"Starting batch of {len(items)} items")
        errors = 0

        for i, item in enumerate(items):
            with agentwatch.trace(f"{name}:item-{i}", parent=batch_span) as item_span:
                try:
                    result = process_fn(item)
                    results.append({"item": item, "result": result, "error": None})
                except Exception as e:
                    errors += 1
                    item_span.set_error(str(e))
                    results.append({"item": item, "result": None, "error": str(e)})
                    if on_error == "stop":
                        batch_span.event(f"Batch aborted after {i+1} items ({errors} errors)")
                        break

        batch_span.event(f"Batch complete: {len(results)}/{len(items)} processed, {errors} errors")
        if errors > 0:
            batch_span.set_metadata("error_count", errors)

    return results


def with_retry(
    fn: Callable[..., T],
    max_attempts: int = 3,
    trace_name: str | None = None,
    *args: Any,
    **kwargs: Any,
) -> T:
    """
    Execute a function with retries, tracing each attempt.

    Args:
        fn: Function to execute.
        max_attempts: Maximum number of attempts.
        trace_name: Name for the trace. Defaults to fn.__name__.
        *args, **kwargs: Passed to fn.

    Returns:
        Result of successful fn() call.

    Raises:
        The last exception if all attempts fail.
    """
    name = trace_name or getattr(fn, "__name__", "retry-operation")
    last_error = None

    with agentwatch.trace(f"retry:{name}") as span:
        for attempt in range(1, max_attempts + 1):
            try:
                result = fn(*args, **kwargs)
                if attempt > 1:
                    span.event(f"Succeeded on attempt {attempt}/{max_attempts}")
                return result
            except Exception as e:
                last_error = e
                span.event(f"Attempt {attempt}/{max_attempts} failed: {e}")
                if attempt < max_attempts:
                    agentwatch.log(
                        "warn",
                        f"Retry {attempt}/{max_attempts} for {name}: {e}",
                    )

        span.set_error(f"All {max_attempts} attempts failed: {last_error}")
        raise last_error  # type: ignore


# ─── Private helpers ─────────────────────────────────────────────────────


def _safe_repr(obj: Any, max_len: int = 200) -> str:
    """Safe string representation, truncated."""
    try:
        s = repr(obj)
        return s[:max_len] + "..." if len(s) > max_len else s
    except Exception:
        return "<unrepresentable>"


def _extract_token_usage(
    response: Any,
    custom_extractor: Callable[[Any], tuple[int, int]] | None = None,
) -> tuple[int, int]:
    """
    Extract token usage from an LLM response.

    Tries:
    1. Custom extractor if provided
    2. Anthropic response format (.usage.input_tokens / .usage.output_tokens)
    3. OpenAI response format (.usage.prompt_tokens / .usage.completion_tokens)
    4. Dict-based formats
    """
    if custom_extractor:
        try:
            return custom_extractor(response)
        except Exception:
            pass

    # Anthropic format
    try:
        usage = getattr(response, "usage", None)
        if usage:
            inp = getattr(usage, "input_tokens", 0)
            out = getattr(usage, "output_tokens", 0)
            if inp or out:
                return (inp, out)
    except Exception:
        pass

    # OpenAI format
    try:
        usage = getattr(response, "usage", None)
        if usage:
            inp = getattr(usage, "prompt_tokens", 0)
            out = getattr(usage, "completion_tokens", 0)
            if inp or out:
                return (inp, out)
    except Exception:
        pass

    # Dict format
    if isinstance(response, dict):
        usage = response.get("usage", response)
        inp = usage.get("input_tokens", usage.get("prompt_tokens", 0))
        out = usage.get("output_tokens", usage.get("completion_tokens", 0))
        return (inp, out)

    return (0, 0)
