"""
OpenClaw integration for AgentWatch.

Auto-instruments OpenClaw agents with tracing, health checks,
cost tracking, and structured logging. Designed for drop-in use:

    from agentwatch.integrations.openclaw import OpenClawInstrumentation
    
    instrumentation = OpenClawInstrumentation(
        agent_name="my-agent",
        track_costs=True,
        track_health=True,
    )
    instrumentation.start()

Or even simpler — just wrap your agent's entry point:

    import agentwatch
    from agentwatch.integrations.openclaw import auto_instrument
    
    auto_instrument("my-agent")

The integration watches for:
- Session starts/ends → traces
- Tool calls → child spans within traces
- Model invocations → cost tracking (token counts)
- Heartbeats → health check records
- Errors → auto-captured in span context
"""

from __future__ import annotations

import functools
import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generator

import agentwatch
from agentwatch.models import HealthStatus, LogLevel

logger = logging.getLogger("agentwatch.openclaw")


@dataclass
class OpenClawConfig:
    """Configuration for OpenClaw instrumentation."""

    agent_name: str = "openclaw-agent"
    db_path: str | None = None

    # Feature flags
    track_costs: bool = True
    track_health: bool = True
    track_tools: bool = True
    log_sessions: bool = True

    # Cost tracking
    default_model: str = "claude-sonnet-4-20250514"

    # Health check interval (in heartbeats)
    health_check_interval: int = 1  # Every heartbeat

    # Auto-detect agent name from environment
    auto_detect_name: bool = True

    # Custom metadata to attach to all traces
    metadata: dict[str, Any] = field(default_factory=dict)


class OpenClawInstrumentation:
    """
    Auto-instrument an OpenClaw agent with AgentWatch observability.

    Provides:
    - Trace creation for each session/conversation turn
    - Child spans for tool calls
    - Cost tracking for model invocations
    - Health check registration
    - Structured logging integration
    """

    def __init__(self, config: OpenClawConfig | None = None, **kwargs: Any):
        """
        Create instrumentation instance.

        Args:
            config: Full configuration object. If None, creates from kwargs.
            **kwargs: Passed to OpenClawConfig if config is None.
        """
        self.config = config or OpenClawConfig(**kwargs)
        self._active = False
        self._session_span = None
        self._tool_hooks: list[Callable] = []
        self._health_checks: dict[str, Callable] = {}

    def start(self) -> "OpenClawInstrumentation":
        """
        Start instrumentation. Initialises AgentWatch and registers hooks.

        Returns self for chaining.
        """
        if self._active:
            return self

        # Auto-detect agent name from environment
        name = self.config.agent_name
        if self.config.auto_detect_name:
            name = self._detect_agent_name() or name

        # Initialise AgentWatch
        agentwatch.init(
            agent_name=name,
            db_path=self.config.db_path,
            metadata=self.config.metadata,
        )

        # Register default health checks
        if self.config.track_health:
            self._register_default_health_checks()

        self._active = True
        agentwatch.log("info", f"OpenClaw instrumentation started for '{name}'")
        return self

    def stop(self) -> None:
        """Stop instrumentation and clean up."""
        if not self._active:
            return
        self._active = False
        agentwatch.log("info", "OpenClaw instrumentation stopped")
        agentwatch.shutdown()

    @contextmanager
    def session(
        self,
        name: str = "session",
        metadata: dict[str, Any] | None = None,
    ) -> Generator[Any, None, None]:
        """
        Trace an entire agent session/conversation turn.

        Usage:
            with instrumentation.session("process-message") as span:
                # ... handle message ...
                span.event("processed user request")
        """
        if not self._active:
            yield None
            return

        with agentwatch.trace(name, metadata=metadata) as span:
            self._session_span = span
            try:
                yield span
            finally:
                self._session_span = None

    @contextmanager
    def tool_call(
        self,
        tool_name: str,
        params: dict[str, Any] | None = None,
    ) -> Generator[Any, None, None]:
        """
        Trace a tool call as a child span.

        Usage:
            with instrumentation.tool_call("web_search", {"query": "weather"}) as span:
                result = do_search()
                span.event(f"Got {len(result)} results")
        """
        if not self._active:
            yield None
            return

        metadata = {"tool": tool_name}
        if params:
            # Sanitise params — don't log sensitive values
            safe_params = {k: v for k, v in params.items()
                          if k not in ("api_key", "token", "secret", "password")}
            metadata["params"] = safe_params

        parent = self._session_span
        with agentwatch.trace(f"tool:{tool_name}", parent=parent, metadata=metadata) as span:
            try:
                yield span
            except Exception as e:
                span.set_error(f"{type(e).__name__}: {e}")
                raise

    def record_model_usage(
        self,
        model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Record token usage for a model invocation.

        Args:
            model: Model name (defaults to config.default_model).
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.
            metadata: Additional metadata.
        """
        if not self._active or not self.config.track_costs:
            return

        agentwatch.costs.record(
            model=model or self.config.default_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            metadata=metadata or {},
        )

    def log(
        self,
        level: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log a message through AgentWatch."""
        if not self._active:
            return
        agentwatch.log(level, message, metadata or {})

    def register_health_check(self, name: str, fn: Callable) -> None:
        """Register a custom health check."""
        self._health_checks[name] = fn
        if self._active:
            agentwatch.health.register(name, fn)

    def run_health_checks(self) -> dict[str, Any]:
        """Run all registered health checks and return results."""
        if not self._active:
            return {}
        return agentwatch.health.run_all()

    # ─── Private helpers ─────────────────────────────────────────────────

    def _detect_agent_name(self) -> str | None:
        """Try to detect the agent name from OpenClaw environment."""
        # Check environment variables that OpenClaw might set
        for var in ("OPENCLAW_AGENT_NAME", "AGENT_NAME", "OPENCLAW_SESSION_ID"):
            val = os.environ.get(var)
            if val:
                return val

        # Check for openclaw config file
        config_paths = [
            Path.home() / ".openclaw" / "openclaw.json",
            Path("openclaw.json"),
        ]
        for config_path in config_paths:
            if config_path.exists():
                try:
                    with open(config_path) as f:
                        config = json.load(f)
                    return config.get("agent", {}).get("name")
                except (json.JSONDecodeError, KeyError, OSError):
                    pass

        return None

    def _register_default_health_checks(self) -> None:
        """Register built-in health checks for OpenClaw agents."""

        def check_database():
            """Check AgentWatch database is accessible."""
            try:
                agent = agentwatch.get_agent()
                stats = agent.storage.get_stats()
                return {
                    "status": "ok",
                    "message": f"DB accessible, {stats['total_traces']} traces stored",
                    "traces": stats["total_traces"],
                }
            except Exception as e:
                return {"status": "critical", "message": f"DB error: {e}"}

        def check_disk_space():
            """Check available disk space."""
            try:
                import shutil
                usage = shutil.disk_usage(Path.home())
                used_pct = (usage.used / usage.total) * 100
                free_gb = usage.free / (1024 ** 3)
                if used_pct > 95:
                    return {"status": "critical", "message": f"Disk {used_pct:.0f}% full ({free_gb:.1f}GB free)"}
                elif used_pct > 85:
                    return {"status": "warn", "message": f"Disk {used_pct:.0f}% full ({free_gb:.1f}GB free)"}
                return {"status": "ok", "message": f"Disk {used_pct:.0f}% used ({free_gb:.1f}GB free)"}
            except Exception as e:
                return {"status": "unknown", "message": str(e)}

        def check_memory():
            """Check process memory usage."""
            try:
                import resource
                rusage = resource.getrusage(resource.RUSAGE_SELF)
                # maxrss is in KB on Linux
                mem_mb = rusage.ru_maxrss / 1024
                if mem_mb > 1024:
                    return {"status": "warn", "message": f"Memory: {mem_mb:.0f}MB (high)"}
                return {"status": "ok", "message": f"Memory: {mem_mb:.0f}MB"}
            except Exception:
                return {"status": "ok", "message": "Memory check unavailable"}

        agentwatch.health.register("agentwatch-db", check_database)
        agentwatch.health.register("disk-space", check_disk_space)
        agentwatch.health.register("process-memory", check_memory)


def auto_instrument(
    agent_name: str = "openclaw-agent",
    **kwargs: Any,
) -> OpenClawInstrumentation:
    """
    One-liner auto-instrumentation for OpenClaw agents.

    Usage:
        from agentwatch.integrations.openclaw import auto_instrument
        inst = auto_instrument("my-agent")

        # Later, in your agent's main loop:
        with inst.session("handle-message") as span:
            span.event("processing...")
            with inst.tool_call("web_search") as tool_span:
                results = search(query)
                tool_span.event(f"got {len(results)} results")
            inst.record_model_usage(input_tokens=500, output_tokens=200)

    Args:
        agent_name: Name for this agent instance.
        **kwargs: Additional OpenClawConfig options.

    Returns:
        Started OpenClawInstrumentation instance.
    """
    config = OpenClawConfig(agent_name=agent_name, **kwargs)
    inst = OpenClawInstrumentation(config=config)
    return inst.start()
