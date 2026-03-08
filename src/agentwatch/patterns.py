"""
Pattern detection engine for AgentWatch.

Analyses traces, logs, and health check history to detect:
- Recurring error patterns (same error type appearing repeatedly)
- Performance degradation trends (increasing durations)
- Health status trends (stable → degrading → critical)
- Anomalous behaviour (sudden spikes in errors or latency)

Extracted and generalised from self-review.py — but operates on
AgentWatch's own storage rather than .learnings/ markdown files.

Usage:
    from agentwatch.patterns import detect_patterns, detect_trends

    patterns = detect_patterns()
    trends = detect_trends(window_hours=24)
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any


class PatternType(str, Enum):
    """Types of detected patterns."""
    RECURRING_ERROR = "recurring_error"
    PERFORMANCE_DEGRADATION = "performance_degradation"
    HEALTH_TREND = "health_trend"
    ERROR_SPIKE = "error_spike"
    SLOW_TRACE = "slow_trace"


class Severity(str, Enum):
    """Pattern severity levels."""
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


class TrendDirection(str, Enum):
    """Overall trend direction."""
    STABLE = "stable"
    IMPROVING = "improving"
    DEGRADING = "degrading"
    UNSTABLE = "unstable"


@dataclass
class Pattern:
    """A detected pattern or anomaly."""
    type: PatternType
    severity: Severity
    title: str
    description: str
    agent_name: str | None = None
    occurrences: int = 1
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "severity": self.severity.value,
            "title": self.title,
            "description": self.description,
            "agent_name": self.agent_name,
            "occurrences": self.occurrences,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "metadata": self.metadata,
        }


@dataclass
class TrendAnalysis:
    """Overall trend analysis for an agent or the whole system."""
    direction: TrendDirection
    error_rate: float  # percentage
    avg_duration_ms: float | None
    duration_trend: TrendDirection | None  # separate trend for latency
    health_trend: TrendDirection | None
    window_hours: int = 24
    patterns: list[Pattern] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction.value,
            "error_rate": round(self.error_rate, 2),
            "avg_duration_ms": round(self.avg_duration_ms, 2) if self.avg_duration_ms else None,
            "duration_trend": self.duration_trend.value if self.duration_trend else None,
            "health_trend": self.health_trend.value if self.health_trend else None,
            "window_hours": self.window_hours,
            "patterns": [p.to_dict() for p in self.patterns],
            "summary": self.summary,
        }


def detect_patterns(
    agent_name: str | None = None,
    window_hours: int = 24,
    min_occurrences: int = 3,
) -> list[Pattern]:
    """
    Detect patterns across traces, logs, and health checks.

    Scans the last `window_hours` of data looking for:
    - Recurring errors (same error message appearing >= min_occurrences times)
    - Performance degradation (duration increasing over time)
    - Error spikes (sudden increase in error rate)
    - Slow traces (significantly slower than average)

    Args:
        agent_name: Filter to a specific agent. None = all agents.
        window_hours: How far back to look.
        min_occurrences: Minimum occurrences to flag a recurring pattern.

    Returns:
        List of detected Pattern objects, sorted by severity.
    """
    from agentwatch.core import get_agent

    try:
        agent = get_agent()
        storage = agent.storage
    except RuntimeError:
        return []

    patterns: list[Pattern] = []

    # Get recent traces and logs
    traces = storage.get_traces(agent_name=agent_name, limit=500)
    logs = storage.get_logs(agent_name=agent_name, limit=1000)

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()

    # Filter to window
    recent_traces = [t for t in traces if t.get("started_at", "") >= cutoff]
    recent_logs = [l for l in logs if l.get("timestamp", "") >= cutoff]

    # 1. Recurring error patterns
    patterns.extend(_detect_recurring_errors(recent_traces, recent_logs, min_occurrences))

    # 2. Performance degradation
    patterns.extend(_detect_performance_degradation(recent_traces))

    # 3. Error spikes
    patterns.extend(_detect_error_spikes(recent_traces, window_hours))

    # 4. Slow traces
    patterns.extend(_detect_slow_traces(recent_traces))

    # Sort by severity (critical first)
    severity_order = {Severity.CRITICAL: 0, Severity.WARN: 1, Severity.INFO: 2}
    patterns.sort(key=lambda p: severity_order.get(p.severity, 3))

    return patterns


def detect_trends(
    agent_name: str | None = None,
    window_hours: int = 24,
) -> TrendAnalysis:
    """
    Analyse overall trends across all observability data.

    Produces a high-level summary of system health direction,
    combining error rates, latency trends, and health check history.

    Args:
        agent_name: Filter to a specific agent. None = all agents.
        window_hours: How far back to look.

    Returns:
        TrendAnalysis with direction, rates, and detected patterns.
    """
    from agentwatch.core import get_agent

    try:
        agent = get_agent()
        storage = agent.storage
    except RuntimeError:
        return TrendAnalysis(
            direction=TrendDirection.STABLE,
            error_rate=0.0,
            avg_duration_ms=None,
            duration_trend=None,
            health_trend=None,
            window_hours=window_hours,
            summary="Agent not initialised.",
        )

    traces = storage.get_traces(agent_name=agent_name, limit=500)
    health = storage.get_health_latest(agent_name=agent_name)

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    recent_traces = [t for t in traces if t.get("started_at", "") >= cutoff]

    # Error rate
    total = len(recent_traces)
    failed = sum(1 for t in recent_traces if t.get("status") == "failed")
    error_rate = (failed / total * 100) if total > 0 else 0.0

    # Average duration
    durations = [
        t["duration_ms"] for t in recent_traces
        if t.get("duration_ms") is not None
    ]
    avg_duration = statistics.mean(durations) if durations else None

    # Duration trend — compare first half to second half
    duration_trend = _compute_duration_trend(recent_traces)

    # Health trend
    health_trend = _compute_health_trend(health)

    # Get all patterns
    patterns = detect_patterns(agent_name=agent_name, window_hours=window_hours)

    # Overall direction
    direction = _compute_overall_direction(error_rate, duration_trend, health_trend, patterns)

    # Summary
    summary = _generate_summary(direction, error_rate, avg_duration, total, patterns)

    return TrendAnalysis(
        direction=direction,
        error_rate=error_rate,
        avg_duration_ms=avg_duration,
        duration_trend=duration_trend,
        health_trend=health_trend,
        window_hours=window_hours,
        patterns=patterns,
        summary=summary,
    )


# ─── Internal detection functions ─────────────────────────────────────────────

def _detect_recurring_errors(
    traces: list[dict],
    logs: list[dict],
    min_occurrences: int,
) -> list[Pattern]:
    """Find error messages that appear repeatedly."""
    patterns: list[Pattern] = []

    # Group failed traces by error-like name patterns
    error_groups: dict[str, list[dict]] = defaultdict(list)
    for t in traces:
        if t.get("status") == "failed":
            # Use trace name as the grouping key
            error_groups[t.get("name", "unknown")].append(t)

    for name, group in error_groups.items():
        if len(group) >= min_occurrences:
            timestamps = sorted(t.get("started_at", "") for t in group)
            patterns.append(Pattern(
                type=PatternType.RECURRING_ERROR,
                severity=Severity.CRITICAL if len(group) >= min_occurrences * 2 else Severity.WARN,
                title=f"Recurring failure: {name}",
                description=(
                    f"Trace '{name}' has failed {len(group)} times. "
                    f"This may indicate a systemic issue."
                ),
                agent_name=group[0].get("agent_name"),
                occurrences=len(group),
                first_seen=_parse_iso(timestamps[0]),
                last_seen=_parse_iso(timestamps[-1]),
                metadata={"trace_name": name, "trace_ids": [t["id"] for t in group[:10]]},
            ))

    # Group error/critical logs by message prefix (first 80 chars)
    log_errors: dict[str, list[dict]] = defaultdict(list)
    for entry in logs:
        if entry.get("level") in ("error", "critical"):
            key = entry.get("message", "")[:80]
            log_errors[key].append(entry)

    for msg_prefix, group in log_errors.items():
        if len(group) >= min_occurrences:
            timestamps = sorted(e.get("timestamp", "") for e in group)
            patterns.append(Pattern(
                type=PatternType.RECURRING_ERROR,
                severity=Severity.WARN,
                title=f"Recurring log error: {msg_prefix[:60]}...",
                description=(
                    f"Error log '{msg_prefix[:60]}...' appeared {len(group)} times. "
                    f"Check for underlying issue."
                ),
                agent_name=group[0].get("agent_name"),
                occurrences=len(group),
                first_seen=_parse_iso(timestamps[0]),
                last_seen=_parse_iso(timestamps[-1]),
                metadata={"message_prefix": msg_prefix},
            ))

    return patterns


def _detect_performance_degradation(traces: list[dict]) -> list[Pattern]:
    """
    Detect if trace durations are increasing over time.

    Splits traces into two halves (older vs newer) and compares
    average durations. A significant increase suggests degradation.
    """
    patterns: list[Pattern] = []

    # Group by trace name for per-operation analysis
    by_name: dict[str, list[dict]] = defaultdict(list)
    for t in traces:
        if t.get("duration_ms") is not None and t.get("status") == "completed":
            by_name[t.get("name", "unknown")].append(t)

    for name, group in by_name.items():
        if len(group) < 6:  # Need enough data points
            continue

        # Sort by time, split in half
        sorted_group = sorted(group, key=lambda t: t.get("started_at", ""))
        mid = len(sorted_group) // 2
        older = sorted_group[:mid]
        newer = sorted_group[mid:]

        older_avg = statistics.mean(t["duration_ms"] for t in older)
        newer_avg = statistics.mean(t["duration_ms"] for t in newer)

        if older_avg > 0:
            increase_pct = ((newer_avg - older_avg) / older_avg) * 100
        else:
            increase_pct = 0

        # Flag if duration increased by >50%
        if increase_pct > 50:
            severity = Severity.CRITICAL if increase_pct > 100 else Severity.WARN
            patterns.append(Pattern(
                type=PatternType.PERFORMANCE_DEGRADATION,
                severity=severity,
                title=f"Performance degradation: {name}",
                description=(
                    f"Average duration for '{name}' increased by {increase_pct:.0f}% "
                    f"({older_avg:.0f}ms → {newer_avg:.0f}ms)."
                ),
                agent_name=group[0].get("agent_name"),
                occurrences=len(group),
                metadata={
                    "trace_name": name,
                    "older_avg_ms": round(older_avg, 2),
                    "newer_avg_ms": round(newer_avg, 2),
                    "increase_pct": round(increase_pct, 2),
                },
            ))

    return patterns


def _detect_error_spikes(traces: list[dict], window_hours: int) -> list[Pattern]:
    """
    Detect sudden spikes in error rate.

    Compares the most recent quarter of the window to the overall
    rate. A spike is >2x the baseline error rate.
    """
    patterns: list[Pattern] = []

    if len(traces) < 10:
        return patterns

    sorted_traces = sorted(traces, key=lambda t: t.get("started_at", ""))

    # Overall error rate
    total_failed = sum(1 for t in sorted_traces if t.get("status") == "failed")
    overall_rate = total_failed / len(sorted_traces)

    # Recent quarter error rate
    quarter = max(len(sorted_traces) // 4, 1)
    recent = sorted_traces[-quarter:]
    recent_failed = sum(1 for t in recent if t.get("status") == "failed")
    recent_rate = recent_failed / len(recent)

    # Spike if recent rate is > 2x overall and has meaningful numbers
    if recent_rate > overall_rate * 2 and recent_failed >= 3:
        patterns.append(Pattern(
            type=PatternType.ERROR_SPIKE,
            severity=Severity.CRITICAL if recent_rate > 0.5 else Severity.WARN,
            title="Error rate spike detected",
            description=(
                f"Recent error rate ({recent_rate:.0%}) is significantly higher "
                f"than baseline ({overall_rate:.0%}). "
                f"{recent_failed} failures in the last {len(recent)} traces."
            ),
            occurrences=recent_failed,
            metadata={
                "overall_rate": round(overall_rate, 4),
                "recent_rate": round(recent_rate, 4),
                "recent_failures": recent_failed,
                "recent_total": len(recent),
            },
        ))

    return patterns


def _detect_slow_traces(traces: list[dict]) -> list[Pattern]:
    """
    Detect individual traces that are significantly slower than average.

    Uses z-score: traces >2 standard deviations above mean are flagged.
    """
    patterns: list[Pattern] = []

    durations = [
        (t, t["duration_ms"])
        for t in traces
        if t.get("duration_ms") is not None and t.get("status") == "completed"
    ]

    if len(durations) < 5:
        return patterns

    values = [d for _, d in durations]
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0

    if stdev == 0:
        return patterns

    threshold = mean + 2 * stdev
    slow = [(t, d) for t, d in durations if d > threshold]

    for trace_dict, duration in slow[:5]:  # Cap at 5
        z_score = (duration - mean) / stdev
        patterns.append(Pattern(
            type=PatternType.SLOW_TRACE,
            severity=Severity.INFO if z_score < 3 else Severity.WARN,
            title=f"Slow trace: {trace_dict.get('name', 'unknown')}",
            description=(
                f"Trace '{trace_dict.get('name')}' took {duration:.0f}ms "
                f"(average: {mean:.0f}ms, {z_score:.1f}σ above mean)."
            ),
            agent_name=trace_dict.get("agent_name"),
            metadata={
                "trace_id": trace_dict.get("id"),
                "duration_ms": round(duration, 2),
                "mean_ms": round(mean, 2),
                "z_score": round(z_score, 2),
            },
        ))

    return patterns


# ─── Trend computation helpers ────────────────────────────────────────────────

def _compute_duration_trend(traces: list[dict]) -> TrendDirection | None:
    """Compare first-half vs second-half average duration."""
    completed = [
        t for t in traces
        if t.get("duration_ms") is not None and t.get("status") == "completed"
    ]

    if len(completed) < 6:
        return None

    sorted_traces = sorted(completed, key=lambda t: t.get("started_at", ""))
    mid = len(sorted_traces) // 2
    older_avg = statistics.mean(t["duration_ms"] for t in sorted_traces[:mid])
    newer_avg = statistics.mean(t["duration_ms"] for t in sorted_traces[mid:])

    if older_avg == 0:
        return TrendDirection.STABLE

    change_pct = ((newer_avg - older_avg) / older_avg) * 100

    if change_pct > 30:
        return TrendDirection.DEGRADING
    elif change_pct < -20:
        return TrendDirection.IMPROVING
    else:
        return TrendDirection.STABLE


def _compute_health_trend(health_results: list[dict]) -> TrendDirection | None:
    """Determine overall health trend from latest results."""
    if not health_results:
        return None

    statuses = [h.get("status", "unknown") for h in health_results]
    critical = sum(1 for s in statuses if s == "critical")
    warn = sum(1 for s in statuses if s == "warn")
    total = len(statuses)

    if critical / total > 0.3:
        return TrendDirection.DEGRADING
    elif (critical + warn) / total > 0.5:
        return TrendDirection.UNSTABLE
    elif critical == 0 and warn == 0:
        return TrendDirection.STABLE
    else:
        return TrendDirection.IMPROVING


def _compute_overall_direction(
    error_rate: float,
    duration_trend: TrendDirection | None,
    health_trend: TrendDirection | None,
    patterns: list[Pattern],
) -> TrendDirection:
    """Combine signals into an overall direction."""
    critical_patterns = sum(1 for p in patterns if p.severity == Severity.CRITICAL)

    if error_rate > 30 or critical_patterns >= 3:
        return TrendDirection.DEGRADING
    if duration_trend == TrendDirection.DEGRADING and error_rate > 10:
        return TrendDirection.DEGRADING
    if health_trend == TrendDirection.DEGRADING:
        return TrendDirection.DEGRADING

    if error_rate > 15 or critical_patterns >= 1:
        return TrendDirection.UNSTABLE
    if duration_trend == TrendDirection.UNSTABLE:
        return TrendDirection.UNSTABLE

    if (
        error_rate < 5
        and critical_patterns == 0
        and duration_trend in (TrendDirection.IMPROVING, TrendDirection.STABLE, None)
    ):
        if duration_trend == TrendDirection.IMPROVING:
            return TrendDirection.IMPROVING
        return TrendDirection.STABLE

    return TrendDirection.STABLE


def _generate_summary(
    direction: TrendDirection,
    error_rate: float,
    avg_duration: float | None,
    trace_count: int,
    patterns: list[Pattern],
) -> str:
    """Generate a human-readable summary."""
    emoji = {
        TrendDirection.STABLE: "✅",
        TrendDirection.IMPROVING: "📈",
        TrendDirection.DEGRADING: "🔴",
        TrendDirection.UNSTABLE: "⚠️",
    }

    parts = [f"{emoji.get(direction, '?')} System is {direction.value}."]

    if trace_count > 0:
        parts.append(f"{trace_count} traces analysed, {error_rate:.1f}% error rate.")
    else:
        parts.append("No traces in window.")

    if avg_duration is not None:
        parts.append(f"Average duration: {avg_duration:.0f}ms.")

    critical = [p for p in patterns if p.severity == Severity.CRITICAL]
    if critical:
        parts.append(f"{len(critical)} critical pattern(s) detected.")

    return " ".join(parts)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_iso(ts: str | None) -> datetime | None:
    """Parse an ISO timestamp string."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
