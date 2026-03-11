"""
LiteLLM callback integration for AgentWatch.

Automatically traces all LLM calls made through LiteLLM and records
token usage / costs.

Usage:
    import litellm
    from agentwatch.integrations.litellm import AgentWatchCallback

    litellm.callbacks = [AgentWatchCallback()]

    # All LiteLLM calls are now automatically traced
    response = litellm.completion(
        model="claude-sonnet-4-20250514",
        messages=[{"role": "user", "content": "Hello"}],
    )

Or with the auto-setup helper:

    from agentwatch.integrations.litellm import auto_instrument
    auto_instrument()  # Adds the callback to litellm.callbacks

The callback captures:
- Model name and provider
- Input/output token counts
- Estimated cost (uses LiteLLM's cost if available, falls back to AgentWatch pricing)
- Request duration
- Error tracking for failed calls
- Streaming support
"""

from __future__ import annotations

import time
from typing import Any

import agentwatch
from agentwatch.tracing import _get_current_span


class AgentWatchCallback:
    """
    LiteLLM callback handler that traces calls and records costs.

    Implements the LiteLLM CustomLogger interface (success/failure handlers
    and async variants).

    Args:
        trace_name_prefix: Prefix for trace names (default: "llm").
        record_costs: Whether to record token usage/costs (default: True).
        capture_messages: Whether to log input/output content (default: False,
            for privacy — set True for debugging).
    """

    def __init__(
        self,
        trace_name_prefix: str = "llm",
        record_costs: bool = True,
        capture_messages: bool = False,
    ):
        self.trace_name_prefix = trace_name_prefix
        self.record_costs = record_costs
        self.capture_messages = capture_messages
        self._call_count = 0
        self._error_count = 0
        self._total_tokens = 0
        self._total_cost = 0.0

    def log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        """Called by LiteLLM on successful completion."""
        self._handle_success(kwargs, response_obj, start_time, end_time)

    def log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        """Called by LiteLLM on failed completion."""
        self._handle_failure(kwargs, start_time, end_time)

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        """Async variant — called by LiteLLM on successful async completion."""
        self._handle_success(kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        """Async variant — called by LiteLLM on failed async completion."""
        self._handle_failure(kwargs, start_time, end_time)

    @property
    def stats(self) -> dict[str, Any]:
        """Get callback statistics."""
        return {
            "calls": self._call_count,
            "errors": self._error_count,
            "total_tokens": self._total_tokens,
            "total_cost_usd": round(self._total_cost, 6),
        }

    def _handle_success(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        """Process a successful LLM call."""
        model = kwargs.get("model", "unknown")
        litellm_params = kwargs.get("litellm_params", {})
        custom_model = litellm_params.get("model", model)

        # Calculate duration
        duration_ms = _compute_duration_ms(start_time, end_time)

        # Extract token usage
        input_tokens = 0
        output_tokens = 0
        response_cost: float | None = None

        usage = _extract_usage(response_obj)
        if usage:
            input_tokens = usage.get("prompt_tokens", 0) or 0
            output_tokens = usage.get("completion_tokens", 0) or 0

        # Try to get LiteLLM's cost calculation
        response_cost = _extract_litellm_cost(kwargs, response_obj)

        # Build metadata
        metadata: dict[str, Any] = {
            "model": custom_model,
            "provider": _extract_provider(kwargs),
            "duration_ms": duration_ms,
        }

        if self.capture_messages:
            messages = kwargs.get("messages", [])
            if messages:
                metadata["input_preview"] = str(messages[-1].get("content", ""))[:200]
            content = _extract_content(response_obj)
            if content:
                metadata["output_preview"] = content[:200]

        # Create a trace span for this call
        trace_name = f"{self.trace_name_prefix}:{_short_model(custom_model)}"

        # If we're inside an existing trace, use the current span context
        current = _get_current_span()
        if current:
            # Just add an event to the current span
            current.event(
                f"LLM call: {_short_model(custom_model)} "
                f"({input_tokens}→{output_tokens} tokens, "
                f"{duration_ms:.0f}ms)",
                metadata=metadata,
            )
        else:
            # Create a standalone trace
            with agentwatch.trace(trace_name, metadata=metadata) as span:
                span.event(
                    f"{input_tokens} input, {output_tokens} output tokens"
                )

        # Record costs
        if self.record_costs and (input_tokens > 0 or output_tokens > 0):
            agentwatch.costs.record(
                model=custom_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=response_cost,
                metadata={"provider": _extract_provider(kwargs)},
            )

        # Update stats
        self._call_count += 1
        self._total_tokens += input_tokens + output_tokens
        if response_cost:
            self._total_cost += response_cost

    def _handle_failure(
        self,
        kwargs: dict[str, Any],
        start_time: Any,
        end_time: Any,
    ) -> None:
        """Process a failed LLM call."""
        model = kwargs.get("model", "unknown")
        duration_ms = _compute_duration_ms(start_time, end_time)

        exception = kwargs.get("exception", None)
        error_msg = str(exception) if exception else "Unknown error"

        trace_name = f"{self.trace_name_prefix}:{_short_model(model)}:error"

        current = _get_current_span()
        if current:
            current.event(f"LLM error: {_short_model(model)} — {error_msg}")
        else:
            with agentwatch.trace(trace_name) as span:
                span.set_error(error_msg)
                span.event(f"Failed after {duration_ms:.0f}ms")

        agentwatch.log("error", f"LLM call failed: {_short_model(model)}", {
            "model": model,
            "error": error_msg[:500],
            "duration_ms": duration_ms,
        })

        self._call_count += 1
        self._error_count += 1


def auto_instrument(
    trace_name_prefix: str = "llm",
    record_costs: bool = True,
    capture_messages: bool = False,
) -> AgentWatchCallback:
    """
    Auto-instrument LiteLLM by adding an AgentWatch callback.

    Call this once at startup. Requires `litellm` to be installed.

    Args:
        trace_name_prefix: Prefix for trace names.
        record_costs: Record token usage and costs.
        capture_messages: Log input/output content (privacy-sensitive).

    Returns:
        The callback instance (for stats access).

    Raises:
        ImportError: If litellm is not installed.
    """
    try:
        import litellm
    except ImportError:
        raise ImportError(
            "LiteLLM is not installed. Install it with: pip install litellm"
        )

    callback = AgentWatchCallback(
        trace_name_prefix=trace_name_prefix,
        record_costs=record_costs,
        capture_messages=capture_messages,
    )

    if not hasattr(litellm, "callbacks") or litellm.callbacks is None:
        litellm.callbacks = []

    litellm.callbacks.append(callback)
    return callback


# ─── Private helpers ─────────────────────────────────────────────────────

def _compute_duration_ms(start_time: Any, end_time: Any) -> float:
    """Compute duration in ms from start/end time objects."""
    try:
        if hasattr(start_time, "timestamp") and hasattr(end_time, "timestamp"):
            ms: float = (end_time.timestamp() - start_time.timestamp()) * 1000
            return ms
        return 0.0
    except Exception:
        return 0.0


def _extract_usage(response_obj: Any) -> dict[str, int] | None:
    """Extract token usage from a LiteLLM response."""
    try:
        # ModelResponse object
        usage = getattr(response_obj, "usage", None)
        if usage:
            if hasattr(usage, "prompt_tokens"):
                return {
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                    "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                    "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                }
            if isinstance(usage, dict):
                return usage
    except Exception:
        pass

    # Dict response
    if isinstance(response_obj, dict):
        return response_obj.get("usage")

    return None


def _extract_litellm_cost(kwargs: dict[str, Any], response_obj: Any) -> float | None:
    """Try to get the cost calculated by LiteLLM."""
    try:
        # LiteLLM adds _hidden_params with response_cost
        hidden = getattr(response_obj, "_hidden_params", {})
        if isinstance(hidden, dict) and "response_cost" in hidden:
            cost: float = float(hidden["response_cost"])
            return cost
    except Exception:
        pass

    try:
        # Some versions put it in additional_args
        additional = kwargs.get("additional_args", {})
        if isinstance(additional, dict) and "response_cost" in additional:
            alt_cost: float = float(additional["response_cost"])
            return alt_cost
    except Exception:
        pass

    return None


def _extract_content(response_obj: Any) -> str:
    """Extract the text content from a response."""
    try:
        choices = getattr(response_obj, "choices", None)
        if choices and len(choices) > 0:
            message = getattr(choices[0], "message", None)
            if message:
                content: str = getattr(message, "content", "") or ""
                return content
    except Exception:
        pass

    if isinstance(response_obj, dict):
        choices = response_obj.get("choices", [])
        if choices:
            result: str = choices[0].get("message", {}).get("content", "")
            return result

    return ""


def _extract_provider(kwargs: dict[str, Any]) -> str:
    """Extract the provider name from kwargs."""
    litellm_params = kwargs.get("litellm_params", {})
    if isinstance(litellm_params, dict):
        custom_provider = litellm_params.get("custom_llm_provider", "")
        if custom_provider:
            return str(custom_provider)
        api_base = litellm_params.get("api_base", "")
        if api_base:
            return str(api_base)

    model: str = str(kwargs.get("model", ""))
    if "/" in model:
        return model.split("/")[0]

    return "unknown"


def _short_model(model: str) -> str:
    """Shorten a model name for trace display."""
    # Remove provider prefix
    if "/" in model:
        model = model.split("/")[-1]
    # Common shortenings
    shorts = {
        "claude-sonnet-4-20250514": "sonnet-4",
        "claude-opus-4-20250514": "opus-4",
        "claude-haiku-3-20250401": "haiku-3",
        "gpt-4o": "gpt-4o",
        "gpt-4o-mini": "gpt-4o-mini",
        "gpt-4.1": "gpt-4.1",
    }
    return shorts.get(model, model[:30])
