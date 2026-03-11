"""
Cron job monitoring for AgentWatch.

Record the outcome of any scheduled job — works with any scheduler
(cron, APScheduler, Celery Beat, OpenClaw, etc.).

Usage::

    import agentwatch

    # Basic usage
    agentwatch.record_cron_run("daily-report", status="ok", duration_ms=1234)

    # With error detail
    agentwatch.record_cron_run(
        "fetch-prices",
        status="error",
        duration_ms=500,
        error="Connection timed out",
    )

    # Context manager for automatic timing and error capture
    with agentwatch.cron_run("ingest-data") as run:
        do_work()  # exceptions are auto-captured; ctx["status"] reflects outcome
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Generator

import agentwatch.core as _core


def record_cron_run(
    job_name: str,
    status: str,
    duration_ms: float | None = None,
    error: str | None = None,
    agent_name: str | None = None,
) -> str | None:
    """
    Record the outcome of a scheduled job.

    Args:
        job_name: Unique name for the job (e.g. "daily-report").
        status: Outcome — "ok", "error", or "timeout".
        duration_ms: How long the job took in milliseconds.
        error: Error message if status is "error" or "timeout".
        agent_name: Override the agent name. Defaults to the current agent.

    Returns:
        The record ID, or None if AgentWatch is not initialised.
    """
    agent = _core._agent
    if agent is None:
        return None

    resolved_agent = agent_name or agent.name
    return agent.storage.record_cron_run(
        job_name=job_name,
        status=status,
        duration_ms=duration_ms,
        error=error,
        agent_name=resolved_agent,
    )


@contextmanager
def cron_run(
    job_name: str,
    agent_name: str | None = None,
) -> Generator[dict, None, None]:
    """
    Context manager that times a job and records the outcome automatically.

    Yields a context dict that reflects the run result after completion.
    Exceptions are captured as errors and then re-raised.

    Usage::

        with agentwatch.cron_run("my-job") as ctx:
            do_work()
        # ctx["status"]      → "ok" or "error"
        # ctx["duration_ms"] → elapsed milliseconds
        # ctx["error"]       → exception message if failed, else None

    Example with error handling::

        try:
            with agentwatch.cron_run("risky-job"):
                risky_operation()
        except Exception:
            pass  # already recorded; handle or ignore
    """
    ctx: dict = {"status": "ok", "duration_ms": None, "error": None}
    start = time.monotonic()
    try:
        yield ctx
        ctx["status"] = "ok"
    except Exception as exc:
        ctx["status"] = "error"
        ctx["error"] = str(exc)
        raise
    finally:
        ctx["duration_ms"] = (time.monotonic() - start) * 1000
        record_cron_run(
            job_name=job_name,
            status=ctx["status"],
            duration_ms=ctx["duration_ms"],
            error=ctx.get("error"),
            agent_name=agent_name,
        )
