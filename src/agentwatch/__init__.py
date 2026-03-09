"""
AgentWatch — Lightweight observability for autonomous AI agents.

Quick start::

    import agentwatch

    agentwatch.init("my-agent")

    with agentwatch.trace("process-email") as span:
        span.event("found 3 unread emails")
        # ... do work ...

    agentwatch.health.register("api", lambda: requests.get(url).ok)
    agentwatch.log("info", "Agent started successfully")

Dashboard::

    agentwatch serve
    # → http://localhost:8470
"""

from agentwatch.core import init, shutdown, get_agent
from agentwatch.tracing import trace
from agentwatch.async_tracing import async_trace
from agentwatch.logging import log
from agentwatch import health
from agentwatch import costs
from agentwatch import patterns
from agentwatch import alerts
from agentwatch import reports
from agentwatch import retention
from agentwatch import config
from agentwatch import auth

__version__ = "0.1.0"
__all__ = [
    "init", "shutdown", "get_agent",
    "trace", "async_trace", "log",
    "health", "costs", "patterns", "alerts", "reports",
    "retention", "config", "auth",
    "__version__",
]
