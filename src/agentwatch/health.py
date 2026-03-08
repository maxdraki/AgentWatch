"""
Health check system for AgentWatch.

Register named health checks and run them on demand or on a schedule.
Inspired by our existing health-monitor.py but generalised for any agent.

Usage:
    import agentwatch

    agentwatch.health.register("database", lambda: db.ping())
    agentwatch.health.register("api", check_api_health)

    results = agentwatch.health.run_all()
    # [HealthCheck(name="database", status="ok", ...), ...]
"""

from __future__ import annotations

import time
import traceback
from typing import Any, Callable

from agentwatch.models import HealthCheck, HealthStatus


def register(name: str, fn: Callable[[], bool | str | dict]) -> None:
    """
    Register a health check function.

    The function should:
    - Return True or "ok" for healthy
    - Return False, raise an exception, or return a string for unhealthy
    - Return a dict with {"status": "ok"|"warn"|"critical", "message": "..."} for fine control

    Args:
        name: Human-readable name for this check (e.g., "database", "api").
        fn: Callable that performs the check.
    """
    from agentwatch.core import get_agent
    agent = get_agent()
    agent.register_health_check(name, fn)


def run(name: str) -> HealthCheck:
    """
    Run a single named health check.

    Args:
        name: The registered check name.

    Returns:
        HealthCheck result.
    """
    from agentwatch.core import get_agent
    agent = get_agent()

    checks = agent.get_health_checks()
    if name not in checks:
        raise KeyError(f"No health check registered with name '{name}'")

    return _execute_check(name, checks[name], agent.name)


def run_all() -> list[HealthCheck]:
    """
    Run all registered health checks.

    Returns:
        List of HealthCheck results, one per registered check.
    """
    from agentwatch.core import get_agent
    agent = get_agent()

    checks = agent.get_health_checks()
    results = []
    for check_name, fn in checks.items():
        result = _execute_check(check_name, fn, agent.name)
        results.append(result)

    return results


def status() -> dict[str, Any]:
    """
    Get a summary of current health status.

    Returns:
        Dict with overall status, individual check results, and metadata.
    """
    results = run_all()

    overall = HealthStatus.OK
    for r in results:
        if r.status == HealthStatus.CRITICAL:
            overall = HealthStatus.CRITICAL
            break
        if r.status == HealthStatus.WARN and overall != HealthStatus.CRITICAL:
            overall = HealthStatus.WARN

    return {
        "overall": overall.value,
        "checks": {r.name: r.to_dict() for r in results},
        "check_count": len(results),
    }


def _execute_check(name: str, fn: Callable, agent_name: str) -> HealthCheck:
    """Execute a health check function and return a HealthCheck result."""
    from agentwatch.core import get_agent

    start = time.monotonic()
    try:
        result = fn()
        duration_ms = (time.monotonic() - start) * 1000

        if isinstance(result, dict):
            status_val = result.get("status", "ok")
            message = result.get("message", "")
            metadata = {k: v for k, v in result.items() if k not in ("status", "message")}
            check_status = _parse_status(status_val)
        elif isinstance(result, bool):
            check_status = HealthStatus.OK if result else HealthStatus.CRITICAL
            message = "healthy" if result else "check returned False"
            metadata = {}
        elif isinstance(result, str):
            if result.lower() in ("ok", "healthy", "good"):
                check_status = HealthStatus.OK
                message = result
            else:
                check_status = HealthStatus.WARN
                message = result
            metadata = {}
        else:
            check_status = HealthStatus.OK
            message = str(result) if result is not None else "ok"
            metadata = {}

    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        check_status = HealthStatus.CRITICAL
        message = f"{type(exc).__name__}: {exc}"
        metadata = {"traceback": traceback.format_exc()}

    check = HealthCheck(
        name=name,
        agent_name=agent_name,
        status=check_status,
        message=message,
        duration_ms=round(duration_ms, 2),
        metadata=metadata,
    )

    # Persist
    try:
        agent = get_agent()
        agent.storage.save_health_check(check)
    except RuntimeError:
        pass

    return check


def _parse_status(value: str) -> HealthStatus:
    """Parse a status string into HealthStatus enum."""
    mapping = {
        "ok": HealthStatus.OK,
        "healthy": HealthStatus.OK,
        "good": HealthStatus.OK,
        "warn": HealthStatus.WARN,
        "warning": HealthStatus.WARN,
        "critical": HealthStatus.CRITICAL,
        "error": HealthStatus.CRITICAL,
        "fail": HealthStatus.CRITICAL,
        "failed": HealthStatus.CRITICAL,
    }
    return mapping.get(value.lower(), HealthStatus.UNKNOWN)
