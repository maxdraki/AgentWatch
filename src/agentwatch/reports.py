"""
Report generation for AgentWatch.

Generate summary reports for agent observability data. Useful for
daily briefings, weekly reviews, or on-demand status checks.

Usage:
    import agentwatch

    # Generate a text summary
    report = agentwatch.reports.summary(hours=24)
    print(report)

    # Generate structured data
    data = agentwatch.reports.summary_data(hours=24)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def summary_data(
    hours: int = 24,
    agent_name: str | None = None,
) -> dict[str, Any]:
    """
    Generate a structured summary of agent activity.

    Args:
        hours: Time window for the report.
        agent_name: Optional filter for specific agent.

    Returns:
        Dict with all summary data.
    """
    from agentwatch.core import get_agent

    agent = get_agent()
    storage = agent.storage

    stats = storage.get_stats(agent_name=agent_name)
    health = storage.get_health_latest(agent_name=agent_name)
    cost_summary = storage.get_cost_summary(agent_name=agent_name, hours=hours)

    # Get traces from time window
    all_traces = storage.get_traces(agent_name=agent_name, limit=500)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent_traces = []
    for t in all_traces:
        try:
            ts = t.get("started_at", "")
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt >= cutoff:
                recent_traces.append(t)
        except (ValueError, TypeError):
            continue

    # Trace stats for period
    total_in_period = len(recent_traces)
    failed_in_period = sum(1 for t in recent_traces if t.get("status") == "failed")
    completed_in_period = sum(1 for t in recent_traces if t.get("status") == "completed")
    durations = [t["duration_ms"] for t in recent_traces if t.get("duration_ms")]
    avg_duration = sum(durations) / len(durations) if durations else 0
    max_duration = max(durations) if durations else 0
    min_duration = min(durations) if durations else 0

    # Error rate for period
    error_rate = (failed_in_period / total_in_period * 100) if total_in_period > 0 else 0

    # Health summary
    health_ok = sum(1 for h in health if h["status"] == "ok")
    health_warn = sum(1 for h in health if h["status"] == "warn")
    health_critical = sum(1 for h in health if h["status"] == "critical")

    # Recent errors
    recent_logs = storage.get_logs(agent_name=agent_name, limit=200)
    recent_errors = [
        log for log in recent_logs
        if log.get("level") in ("error", "critical")
    ][:10]

    # Top failing traces
    failed_traces = [t for t in recent_traces if t.get("status") == "failed"]
    fail_counts: dict[str, int] = {}
    for t in failed_traces:
        name = t.get("name", "unknown")
        fail_counts[name] = fail_counts.get(name, 0) + 1
    top_failures = sorted(fail_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "period_hours": hours,
        "agent_name": agent_name or "all",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "traces": {
            "total": total_in_period,
            "completed": completed_in_period,
            "failed": failed_in_period,
            "error_rate_pct": round(error_rate, 1),
            "avg_duration_ms": round(avg_duration, 1),
            "max_duration_ms": round(max_duration, 1),
            "min_duration_ms": round(min_duration, 1),
        },
        "health": {
            "ok": health_ok,
            "warn": health_warn,
            "critical": health_critical,
            "checks": [
                {"name": h["name"], "status": h["status"], "message": h.get("message", "")}
                for h in health
            ],
        },
        "costs": {
            "total_usd": cost_summary.get("total_cost_usd", 0),
            "total_tokens": cost_summary.get("total_tokens", 0),
            "by_model": cost_summary.get("by_model", []),
        },
        "top_failures": [{"name": n, "count": c} for n, c in top_failures],
        "recent_errors": [
            {"level": e["level"], "message": e["message"], "timestamp": e["timestamp"]}
            for e in recent_errors
        ],
        "overall_stats": stats,
    }


def summary(
    hours: int = 24,
    agent_name: str | None = None,
) -> str:
    """
    Generate a human-readable summary report.

    Args:
        hours: Time window for the report.
        agent_name: Optional filter for specific agent.

    Returns:
        Formatted text report.
    """
    data = summary_data(hours=hours, agent_name=agent_name)
    lines = []

    # Header
    lines.append("=" * 60)
    lines.append(f"  AgentWatch Report — Last {hours}h")
    lines.append(f"  Agent: {data['agent_name']}")
    lines.append(f"  Generated: {data['generated_at'][:19].replace('T', ' ')} UTC")
    lines.append("=" * 60)

    # Health
    h = data["health"]
    if h["critical"] > 0:
        status = "🔴 CRITICAL"
    elif h["warn"] > 0:
        status = "🟡 WARNING"
    else:
        status = "🟢 HEALTHY"

    lines.append(f"\n  Health: {status}")
    lines.append(f"    OK: {h['ok']}  |  Warn: {h['warn']}  |  Critical: {h['critical']}")
    for check in h["checks"]:
        emoji = {"ok": "🟢", "warn": "🟡", "critical": "🔴"}.get(check["status"], "⚪")
        lines.append(f"    {emoji} {check['name']}: {check['message']}")

    # Traces
    t = data["traces"]
    lines.append(f"\n  Traces: {t['total']} total")
    lines.append(f"    ✅ Completed: {t['completed']}  |  ❌ Failed: {t['failed']}")
    lines.append(f"    Error rate: {t['error_rate_pct']}%")
    if t["total"] > 0:
        lines.append(f"    Duration: avg {_fmt_ms(t['avg_duration_ms'])} / max {_fmt_ms(t['max_duration_ms'])}")

    # Top failures
    if data["top_failures"]:
        lines.append(f"\n  Top Failures:")
        for f in data["top_failures"]:
            lines.append(f"    ❌ {f['name']}: {f['count']}x")

    # Costs
    c = data["costs"]
    if c["total_usd"] > 0:
        lines.append(f"\n  Costs: ${c['total_usd']:.4f}")
        lines.append(f"    Tokens: {c['total_tokens']:,}")
        for m in c["by_model"][:3]:
            lines.append(f"    {m['model']}: ${m['cost_usd']:.4f} ({m['count']} calls)")

    # Recent errors
    if data["recent_errors"]:
        lines.append(f"\n  Recent Errors:")
        for e in data["recent_errors"][:5]:
            ts = e["timestamp"][:16].replace("T", " ")
            lines.append(f"    [{ts}] {e['message']}")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def _fmt_ms(ms: float) -> str:
    """Format milliseconds for display."""
    if ms < 1000:
        return f"{ms:.0f}ms"
    if ms < 60000:
        return f"{ms / 1000:.1f}s"
    return f"{ms / 60000:.1f}m"
