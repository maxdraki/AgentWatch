"""
LangChain callback integration for AgentWatch.

Automatically traces all LangChain LLM calls, chain runs, tool invocations,
and retriever queries. Records token usage and costs.

Usage::

    from agentwatch.integrations.langchain import AgentWatchHandler

    handler = AgentWatchHandler()

    # Pass to any LangChain component
    llm = ChatOpenAI(callbacks=[handler])
    chain = prompt | llm
    chain.invoke({"input": "hello"}, config={"callbacks": [handler]})

Or use the auto-setup helper::

    from agentwatch.integrations.langchain import auto_instrument
    auto_instrument()  # Installs a global callback handler

The handler captures:
    - LLM/chat model calls with model name, tokens, and cost
    - Chain execution as nested traces
    - Tool calls with input/output
    - Retriever queries
    - Errors at every level
    - Streaming token counts (when available)

Requires ``langchain-core`` to be installed. AgentWatch itself has no
dependency on LangChain — this is an optional integration.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional, Sequence, Union

import agentwatch
from agentwatch.tracing import trace as _trace, _get_current_span


# Type aliases for LangChain types (avoid hard import at module level)
_LLMResult = Any
_ChatGeneration = Any
_BaseMessage = Any


def _gen_id() -> str:
    return uuid.uuid4().hex[:16]


class AgentWatchHandler:
    """
    LangChain callback handler that traces calls and records costs.

    Implements the LangChain ``BaseCallbackHandler`` interface without
    inheriting from it, so ``langchain-core`` doesn't need to be installed
    at import time. LangChain duck-types callback handlers, so this works
    as long as the methods exist.

    Args:
        trace_name_prefix: Prefix for trace/span names (default: "langchain").
        record_costs: Whether to record token usage/costs (default: True).
        capture_io: Whether to log input/output content in span metadata
            (default: False, for privacy).
    """

    # LangChain checks these flags to decide which callbacks to fire
    ignore_llm: bool = False
    ignore_chain: bool = False
    ignore_agent: bool = False
    ignore_retriever: bool = False
    ignore_retry: bool = False
    ignore_chat_model: bool = False
    raise_error: bool = False

    def __init__(
        self,
        trace_name_prefix: str = "langchain",
        record_costs: bool = True,
        capture_io: bool = False,
    ):
        self.trace_name_prefix = trace_name_prefix
        self.record_costs = record_costs
        self.capture_io = capture_io

        # Track active runs: run_id → (span_context, start_time, metadata)
        self._active_runs: dict[str, dict[str, Any]] = {}

        # Stats
        self._call_count = 0
        self._error_count = 0
        self._total_tokens = 0
        self._total_cost = 0.0

    @property
    def stats(self) -> dict[str, Any]:
        """Return usage statistics."""
        return {
            "calls": self._call_count,
            "errors": self._error_count,
            "total_tokens": self._total_tokens,
            "total_cost_usd": round(self._total_cost, 6),
        }

    # ─── LLM Callbacks ───────────────────────────────────────────────────

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Called when an LLM starts generating."""
        run_key = str(run_id)
        model_name = (
            serialized.get("kwargs", {}).get("model_name")
            or serialized.get("kwargs", {}).get("model")
            or serialized.get("id", ["unknown"])[-1]
        )

        name = f"{self.trace_name_prefix}.llm.{model_name}"
        span_ctx = _trace(name)
        span = span_ctx.__enter__()

        meta: dict[str, Any] = {"model": model_name, "type": "llm"}
        if tags:
            meta["tags"] = tags
        if self.capture_io:
            meta["prompts"] = prompts[:3]  # Limit to avoid huge metadata

        
            for k, v in meta.items():
                span.set_metadata(k, v)

        self._active_runs[run_key] = {
            "span_ctx": span_ctx,
            "span": span,
            "start_time": time.time(),
            "model": model_name,
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
        }

    def on_chat_model_start(
        self,
        serialized: Dict[str, Any],
        messages: List[List[Any]],
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Called when a chat model starts (preferred over on_llm_start for chat models)."""
        run_key = str(run_id)
        model_name = (
            kwargs.get("invocation_params", {}).get("model_name")
            or kwargs.get("invocation_params", {}).get("model")
            or serialized.get("kwargs", {}).get("model_name")
            or serialized.get("kwargs", {}).get("model")
            or serialized.get("id", ["unknown"])[-1]
        )

        name = f"{self.trace_name_prefix}.chat.{model_name}"
        span_ctx = _trace(name)
        span = span_ctx.__enter__()

        meta: dict[str, Any] = {"model": model_name, "type": "chat_model"}
        if tags:
            meta["tags"] = tags
        if self.capture_io and messages:
            # Log message count per conversation
            meta["message_counts"] = [len(conv) for conv in messages]

        
            for k, v in meta.items():
                span.set_metadata(k, v)

        self._active_runs[run_key] = {
            "span_ctx": span_ctx,
            "span": span,
            "start_time": time.time(),
            "model": model_name,
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
        }

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Called when an LLM finishes generating."""
        run_key = str(run_id)
        run_data = self._active_runs.pop(run_key, None)
        if not run_data:
            return

        self._call_count += 1
        span = run_data["span"]
        span_ctx = run_data["span_ctx"]
        model = run_data["model"]

        # Extract token usage from LLM response
        token_usage = {}
        if hasattr(response, "llm_output") and response.llm_output:
            token_usage = response.llm_output.get("token_usage", {})
        elif hasattr(response, "generations") and response.generations:
            # Try to get from generation info
            for gen_list in response.generations:
                for gen in gen_list:
                    if hasattr(gen, "generation_info") and gen.generation_info:
                        token_usage = gen.generation_info.get("token_usage", token_usage)

        input_tokens = token_usage.get("prompt_tokens", 0) or token_usage.get("input_tokens", 0)
        output_tokens = token_usage.get("completion_tokens", 0) or token_usage.get("output_tokens", 0)
        total_tokens = token_usage.get("total_tokens", 0) or (input_tokens + output_tokens)

        if total_tokens > 0:
            self._total_tokens += total_tokens
            span.set_metadata("tokens", {
                "input": input_tokens,
                "output": output_tokens,
                "total": total_tokens,
            })

            if self.record_costs:
                try:
                    cost_record = agentwatch.costs.record(
                        model=model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
                    self._total_cost += cost_record.estimated_cost_usd
                except Exception:
                    pass

        if self.capture_io and hasattr(response, "generations"):
            try:
                texts = []
                for gen_list in response.generations:
                    for gen in gen_list:
                        if hasattr(gen, "text"):
                            texts.append(gen.text[:200])
                        elif hasattr(gen, "message") and hasattr(gen.message, "content"):
                            content = gen.message.content
                            if isinstance(content, str):
                                texts.append(content[:200])
                if texts:
                    span.set_metadata("output_preview", texts)
            except Exception:
                pass

        span_ctx.__exit__(None, None, None)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Called when an LLM errors."""
        run_key = str(run_id)
        run_data = self._active_runs.pop(run_key, None)
        if not run_data:
            return

        self._error_count += 1
        span_ctx = run_data["span_ctx"]
        span_ctx.__exit__(type(error), error, error.__traceback__)

    # ─── Chain Callbacks ─────────────────────────────────────────────────

    def on_chain_start(
        self,
        serialized: Dict[str, Any],
        inputs: Dict[str, Any],
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Called when a chain starts running."""
        run_key = str(run_id)
        chain_name = serialized.get("name") or serialized.get("id", ["unknown"])[-1]

        name = f"{self.trace_name_prefix}.chain.{chain_name}"
        span_ctx = _trace(name)
        span = span_ctx.__enter__()

        meta: dict[str, Any] = {"type": "chain", "chain_name": chain_name}
        if tags:
            meta["tags"] = tags
        if self.capture_io:
            # Truncate large inputs
            meta["input_keys"] = list(inputs.keys()) if isinstance(inputs, dict) else ["raw"]

        
            for k, v in meta.items():
                span.set_metadata(k, v)

        self._active_runs[run_key] = {
            "span_ctx": span_ctx,
            "span": span,
            "start_time": time.time(),
            "model": None,
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
        }

    def on_chain_end(
        self,
        outputs: Dict[str, Any],
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Called when a chain finishes."""
        run_key = str(run_id)
        run_data = self._active_runs.pop(run_key, None)
        if not run_data:
            return

        span = run_data["span"]
        span_ctx = run_data["span_ctx"]

        if self.capture_io and isinstance(outputs, dict):
            span.set_metadata("output_keys", list(outputs.keys()))

        span_ctx.__exit__(None, None, None)

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Called when a chain errors."""
        run_key = str(run_id)
        run_data = self._active_runs.pop(run_key, None)
        if not run_data:
            return

        self._error_count += 1
        span_ctx = run_data["span_ctx"]
        span_ctx.__exit__(type(error), error, error.__traceback__)

    # ─── Tool Callbacks ──────────────────────────────────────────────────

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Called when a tool starts running."""
        run_key = str(run_id)
        tool_name = serialized.get("name") or serialized.get("id", ["unknown"])[-1]

        name = f"{self.trace_name_prefix}.tool.{tool_name}"
        span_ctx = _trace(name)
        span = span_ctx.__enter__()

        meta: dict[str, Any] = {"type": "tool", "tool_name": tool_name}
        if tags:
            meta["tags"] = tags
        if self.capture_io:
            meta["input"] = input_str[:500] if input_str else ""

        
            for k, v in meta.items():
                span.set_metadata(k, v)

        self._active_runs[run_key] = {
            "span_ctx": span_ctx,
            "span": span,
            "start_time": time.time(),
            "model": None,
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
        }

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Called when a tool finishes."""
        run_key = str(run_id)
        run_data = self._active_runs.pop(run_key, None)
        if not run_data:
            return

        span = run_data["span"]
        span_ctx = run_data["span_ctx"]

        if self.capture_io:
            output_str = str(output)[:500] if output else ""
            span.set_metadata("output", output_str)

        span_ctx.__exit__(None, None, None)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Called when a tool errors."""
        run_key = str(run_id)
        run_data = self._active_runs.pop(run_key, None)
        if not run_data:
            return

        self._error_count += 1
        span_ctx = run_data["span_ctx"]
        span_ctx.__exit__(type(error), error, error.__traceback__)

    # ─── Retriever Callbacks ─────────────────────────────────────────────

    def on_retriever_start(
        self,
        serialized: Dict[str, Any],
        query: str,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Called when a retriever starts a query."""
        run_key = str(run_id)
        retriever_name = serialized.get("name") or serialized.get("id", ["unknown"])[-1]

        name = f"{self.trace_name_prefix}.retriever.{retriever_name}"
        span_ctx = _trace(name)
        span = span_ctx.__enter__()

        meta: dict[str, Any] = {"type": "retriever", "retriever_name": retriever_name}
        if tags:
            meta["tags"] = tags
        if self.capture_io:
            meta["query"] = query[:500]

        
            for k, v in meta.items():
                span.set_metadata(k, v)

        self._active_runs[run_key] = {
            "span_ctx": span_ctx,
            "span": span,
            "start_time": time.time(),
            "model": None,
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
        }

    def on_retriever_end(
        self,
        documents: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Called when a retriever finishes."""
        run_key = str(run_id)
        run_data = self._active_runs.pop(run_key, None)
        if not run_data:
            return

        span = run_data["span"]
        span_ctx = run_data["span_ctx"]

        if documents:
            span.set_metadata("document_count", len(documents))

        span_ctx.__exit__(None, None, None)

    def on_retriever_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Called when a retriever errors."""
        run_key = str(run_id)
        run_data = self._active_runs.pop(run_key, None)
        if not run_data:
            return

        self._error_count += 1
        span_ctx = run_data["span_ctx"]
        span_ctx.__exit__(type(error), error, error.__traceback__)

    # ─── Text/Token Callbacks (optional) ─────────────────────────────────

    def on_llm_new_token(
        self,
        token: str,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Called on each new LLM token (streaming)."""
        # We don't need per-token handling — costs come from on_llm_end
        pass

    def on_text(
        self,
        text: str,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Called with arbitrary text output."""
        pass

    # ─── Agent Callbacks ─────────────────────────────────────────────────

    def on_agent_action(
        self,
        action: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Called when an agent takes an action."""
        run_key = str(run_id)
        run_data = self._active_runs.get(run_key)
        if run_data and run_data["span"]:
            tool = getattr(action, "tool", "unknown")
            run_data["span"].event(f"agent_action: {tool}")

    def on_agent_finish(
        self,
        finish: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Called when an agent finishes."""
        run_key = str(run_id)
        run_data = self._active_runs.get(run_key)
        if run_data and run_data["span"]:
            run_data["span"].event("agent_finish")


def auto_instrument(
    trace_name_prefix: str = "langchain",
    record_costs: bool = True,
    capture_io: bool = False,
) -> AgentWatchHandler:
    """
    Install AgentWatch as a global LangChain callback handler.

    This adds the handler to LangChain's global callback manager so all
    LLM, chain, tool, and retriever calls are automatically traced.

    Args:
        trace_name_prefix: Prefix for trace names.
        record_costs: Whether to record token usage/costs.
        capture_io: Whether to log input/output content.

    Returns:
        The installed handler instance.

    Raises:
        ImportError: If langchain-core is not installed.
    """
    try:
        from langchain_core.globals import set_llm_cache
        from langchain_core.callbacks import CallbackManager
        from langchain_core.callbacks.manager import (
            get_callback_manager,
        )
    except ImportError:
        raise ImportError(
            "langchain-core is required for auto_instrument(). "
            "Install it with: pip install langchain-core"
        )

    handler = AgentWatchHandler(
        trace_name_prefix=trace_name_prefix,
        record_costs=record_costs,
        capture_io=capture_io,
    )

    # Add to the default callback manager
    try:
        # LangChain 0.2+ global callback approach
        import langchain_core
        if hasattr(langchain_core, "callbacks"):
            manager = langchain_core.callbacks.get_callback_manager()
            manager.add_handler(handler)
    except Exception:
        # If global registration fails, the handler still works when passed
        # explicitly to individual calls via callbacks=[handler]
        pass

    return handler
