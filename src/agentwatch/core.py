"""
Core initialisation and global state for AgentWatch.

The design principle: a single global agent instance per process,
configured once with `agentwatch.init()`. All other modules read
from this global state.
"""

from __future__ import annotations

import atexit
import threading
from dataclasses import dataclass, field
from typing import Any

from agentwatch.storage import Storage


@dataclass
class AgentConfig:
    """Configuration for an instrumented agent."""

    agent_name: str = "default"
    db_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    auto_flush: bool = True


class Agent:
    """
    The central AgentWatch agent instance.

    Holds configuration, storage, and registered health checks.
    There's typically one per process, accessed via the module-level
    `get_agent()` function.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.storage = Storage(db_path=config.db_path)
        self._health_checks: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._active = True

    @property
    def name(self) -> str:
        return self.config.agent_name

    def register_health_check(self, name: str, fn: Any) -> None:
        """Register a health check function."""
        with self._lock:
            self._health_checks[name] = fn

    def get_health_checks(self) -> dict[str, Any]:
        """Get all registered health check functions."""
        with self._lock:
            return dict(self._health_checks)

    def shutdown(self) -> None:
        """Clean up resources."""
        if self._active:
            self._active = False
            self.storage.close()


# ─── Global state ────────────────────────────────────────────────────────────

_agent: Agent | None = None
_lock = threading.Lock()


def init(
    agent_name: str = "default",
    db_path: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Agent:
    """
    Initialise AgentWatch for this process.

    Call this once at startup. Subsequent calls with the same agent_name
    return the existing instance; different names raise an error.

    Args:
        agent_name: Human-readable name for this agent.
        db_path: Path to SQLite database. Defaults to ~/.agentwatch/agentwatch.db
        metadata: Optional metadata to attach to all traces from this agent.

    Returns:
        The Agent instance.
    """
    global _agent

    with _lock:
        if _agent is not None:
            if _agent.config.agent_name == agent_name:
                return _agent
            raise RuntimeError(
                f"AgentWatch already initialised as '{_agent.config.agent_name}'. "
                f"Cannot re-init as '{agent_name}'. Call shutdown() first."
            )

        config = AgentConfig(
            agent_name=agent_name,
            db_path=db_path,
            metadata=metadata or {},
        )
        _agent = Agent(config)

        # Auto-cleanup on process exit
        atexit.register(_agent.shutdown)

        return _agent


def get_agent() -> Agent:
    """
    Get the global Agent instance.

    Raises RuntimeError if init() hasn't been called.
    """
    if _agent is None:
        raise RuntimeError(
            "AgentWatch not initialised. Call agentwatch.init('agent-name') first."
        )
    return _agent


def shutdown() -> None:
    """Shut down AgentWatch and release resources."""
    global _agent
    with _lock:
        if _agent:
            _agent.shutdown()
            _agent = None


def _reset() -> None:
    """Reset global state — for testing only."""
    global _agent
    with _lock:
        if _agent:
            _agent.shutdown()
        _agent = None
