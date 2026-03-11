"""
Data retention and cleanup for AgentWatch.

Manages automatic pruning of old data to keep the database lean.
Supports configurable retention periods per data type and manual
cleanup commands.

Usage:
    import agentwatch

    # Prune data older than 30 days
    agentwatch.retention.prune(days=30)

    # Prune with per-type settings
    agentwatch.retention.prune(
        trace_days=30,
        log_days=7,
        health_days=14,
        cost_days=90,
    )

    # Get database size info
    info = agentwatch.retention.db_info()
    # {"size_bytes": 1234567, "size_mb": 1.2, "table_counts": {...}}

CLI:
    agentwatch db info       # Show database statistics
    agentwatch db prune      # Prune old data
    agentwatch db vacuum     # Reclaim disk space
    agentwatch db export     # Export data to JSONL
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TextIO

from agentwatch.storage import Storage


@dataclass
class PruneResult:
    """Result of a prune operation."""

    traces_deleted: int = 0
    spans_deleted: int = 0
    events_deleted: int = 0
    logs_deleted: int = 0
    health_deleted: int = 0
    cost_deleted: int = 0
    metrics_deleted: int = 0

    @property
    def total_deleted(self) -> int:
        return (
            self.traces_deleted
            + self.spans_deleted
            + self.events_deleted
            + self.logs_deleted
            + self.health_deleted
            + self.cost_deleted
            + self.metrics_deleted
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "traces_deleted": self.traces_deleted,
            "spans_deleted": self.spans_deleted,
            "events_deleted": self.events_deleted,
            "logs_deleted": self.logs_deleted,
            "health_deleted": self.health_deleted,
            "cost_deleted": self.cost_deleted,
            "metrics_deleted": self.metrics_deleted,
            "total_deleted": self.total_deleted,
        }

    def summary(self) -> str:
        """Human-readable summary of what was pruned."""
        parts = []
        if self.traces_deleted:
            parts.append(f"{self.traces_deleted} traces")
        if self.spans_deleted:
            parts.append(f"{self.spans_deleted} spans")
        if self.events_deleted:
            parts.append(f"{self.events_deleted} events")
        if self.logs_deleted:
            parts.append(f"{self.logs_deleted} logs")
        if self.health_deleted:
            parts.append(f"{self.health_deleted} health checks")
        if self.cost_deleted:
            parts.append(f"{self.cost_deleted} cost records")
        if self.metrics_deleted:
            parts.append(f"{self.metrics_deleted} metrics")

        if not parts:
            return "Nothing to prune."
        return f"Pruned: {', '.join(parts)} ({self.total_deleted} total rows)"


@dataclass
class DbInfo:
    """Database information and statistics."""

    path: str = ""
    size_bytes: int = 0
    size_mb: float = 0.0
    table_counts: dict[str, int] = None  # type: ignore
    oldest_trace: str | None = None
    newest_trace: str | None = None

    def __post_init__(self):
        if self.table_counts is None:
            self.table_counts = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "size_mb": round(self.size_mb, 2),
            "table_counts": self.table_counts,
            "oldest_trace": self.oldest_trace,
            "newest_trace": self.newest_trace,
        }


def prune(
    days: int | None = None,
    trace_days: int | None = None,
    log_days: int | None = None,
    health_days: int | None = None,
    cost_days: int | None = None,
    metric_days: int | None = None,
    agent_name: str | None = None,
    storage: Storage | None = None,
    dry_run: bool = False,
) -> PruneResult:
    """
    Prune old data from the database.

    Args:
        days: Default retention period (applies to all types unless overridden).
        trace_days: Retention for traces and their spans/events.
        log_days: Retention for log entries.
        health_days: Retention for health check records.
        cost_days: Retention for token usage/cost records.
        agent_name: Only prune data for this agent.
        storage: Storage instance (auto-detected from global agent if not provided).
        dry_run: If True, count what would be deleted without actually deleting.

    Returns:
        PruneResult with counts of deleted rows.
    """
    if storage is None:
        from agentwatch.core import get_agent
        storage = get_agent().storage

    # Resolve retention periods
    default_days = days or 30
    t_days = trace_days or default_days
    l_days = log_days or default_days
    h_days = health_days or default_days
    c_days = cost_days or default_days
    m_days = metric_days or default_days

    now = datetime.now(timezone.utc)
    result = PruneResult()

    with storage._connect() as conn:
        # Agent filter
        agent_where = " AND agent_name = ?" if agent_name else ""
        agent_params = [agent_name] if agent_name else []

        # --- Traces ---
        trace_cutoff = (now - timedelta(days=t_days)).isoformat()

        if dry_run:
            row = conn.execute(
                f"SELECT COUNT(*) FROM traces WHERE started_at < ?{agent_where}",
                [trace_cutoff] + agent_params,
            ).fetchone()
            result.traces_deleted = row[0]
        else:
            # Get trace IDs to delete (for cascading to spans/events)
            old_trace_ids = [
                r[0] for r in conn.execute(
                    f"SELECT id FROM traces WHERE started_at < ?{agent_where}",
                    [trace_cutoff] + agent_params,
                ).fetchall()
            ]

            if old_trace_ids:
                placeholders = ",".join("?" * len(old_trace_ids))

                # Delete span events for old traces
                old_span_ids = [
                    r[0] for r in conn.execute(
                        f"SELECT id FROM spans WHERE trace_id IN ({placeholders})",
                        old_trace_ids,
                    ).fetchall()
                ]

                if old_span_ids:
                    evt_placeholders = ",".join("?" * len(old_span_ids))
                    cursor = conn.execute(
                        f"DELETE FROM span_events WHERE span_id IN ({evt_placeholders})",
                        old_span_ids,
                    )
                    result.events_deleted = cursor.rowcount

                # Delete spans
                cursor = conn.execute(
                    f"DELETE FROM spans WHERE trace_id IN ({placeholders})",
                    old_trace_ids,
                )
                result.spans_deleted = cursor.rowcount

                # Delete traces
                cursor = conn.execute(
                    f"DELETE FROM traces WHERE id IN ({placeholders})",
                    old_trace_ids,
                )
                result.traces_deleted = cursor.rowcount

        # --- Logs ---
        log_cutoff = (now - timedelta(days=l_days)).isoformat()

        if dry_run:
            row = conn.execute(
                f"SELECT COUNT(*) FROM logs WHERE timestamp < ?{agent_where}",
                [log_cutoff] + agent_params,
            ).fetchone()
            result.logs_deleted = row[0]
        else:
            cursor = conn.execute(
                f"DELETE FROM logs WHERE timestamp < ?{agent_where}",
                [log_cutoff] + agent_params,
            )
            result.logs_deleted = cursor.rowcount

        # --- Health checks ---
        health_cutoff = (now - timedelta(days=h_days)).isoformat()

        if dry_run:
            row = conn.execute(
                f"SELECT COUNT(*) FROM health_checks WHERE timestamp < ?{agent_where}",
                [health_cutoff] + agent_params,
            ).fetchone()
            result.health_deleted = row[0]
        else:
            cursor = conn.execute(
                f"DELETE FROM health_checks WHERE timestamp < ?{agent_where}",
                [health_cutoff] + agent_params,
            )
            result.health_deleted = cursor.rowcount

        # --- Token usage / costs ---
        cost_cutoff = (now - timedelta(days=c_days)).isoformat()

        if dry_run:
            row = conn.execute(
                f"SELECT COUNT(*) FROM token_usage WHERE timestamp < ?{agent_where}",
                [cost_cutoff] + agent_params,
            ).fetchone()
            result.cost_deleted = row[0]
        else:
            cursor = conn.execute(
                f"DELETE FROM token_usage WHERE timestamp < ?{agent_where}",
                [cost_cutoff] + agent_params,
            )
            result.cost_deleted = cursor.rowcount

        # --- Metrics ---
        metric_cutoff = (now - timedelta(days=m_days)).isoformat()

        if dry_run:
            row = conn.execute(
                f"SELECT COUNT(*) FROM metrics WHERE timestamp < ?{agent_where}",
                [metric_cutoff] + agent_params,
            ).fetchone()
            result.metrics_deleted = row[0]
        else:
            cursor = conn.execute(
                f"DELETE FROM metrics WHERE timestamp < ?{agent_where}",
                [metric_cutoff] + agent_params,
            )
            result.metrics_deleted = cursor.rowcount

    return result


def vacuum(storage: Storage | None = None) -> int:
    """
    Run VACUUM on the database to reclaim disk space.

    Returns the size reduction in bytes (before - after).
    """
    if storage is None:
        from agentwatch.core import get_agent
        storage = get_agent().storage

    size_before = _get_file_size(storage.db_path)

    # VACUUM must run outside a transaction
    conn = None
    try:
        import sqlite3
        conn = sqlite3.connect(storage.db_path)
        conn.execute("VACUUM")
        conn.close()
    except Exception:
        if conn:
            conn.close()
        raise

    size_after = _get_file_size(storage.db_path)
    return max(0, size_before - size_after)


def db_info(storage: Storage | None = None) -> DbInfo:
    """
    Get database information and statistics.

    Returns:
        DbInfo with size, table counts, and date range.
    """
    if storage is None:
        from agentwatch.core import get_agent
        storage = get_agent().storage

    info = DbInfo(path=storage.db_path)
    info.size_bytes = _get_file_size(storage.db_path)
    info.size_mb = info.size_bytes / (1024 * 1024)

    with storage._connect() as conn:
        tables = ["traces", "spans", "span_events", "logs", "health_checks", "token_usage", "metrics"]
        for table in tables:
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                info.table_counts[table] = row[0]
            except Exception:
                info.table_counts[table] = 0

        # Date range
        oldest = conn.execute(
            "SELECT MIN(started_at) FROM traces"
        ).fetchone()
        if oldest and oldest[0]:
            info.oldest_trace = oldest[0]

        newest = conn.execute(
            "SELECT MAX(started_at) FROM traces"
        ).fetchone()
        if newest and newest[0]:
            info.newest_trace = newest[0]

    return info


def export_jsonl(
    output: str | TextIO,
    tables: list[str] | None = None,
    agent_name: str | None = None,
    hours: int | None = None,
    storage: Storage | None = None,
) -> int:
    """
    Export data to JSONL (JSON Lines) format.

    Each line is a JSON object with a "type" field indicating the data type,
    followed by the data fields. Compatible with jq, pandas, and BigQuery.

    Args:
        output: File path or writable file object.
        tables: Which tables to export. Default: all.
        agent_name: Filter to a specific agent.
        hours: Limit to the last N hours.
        storage: Storage instance.

    Returns:
        Number of lines written.
    """
    if storage is None:
        from agentwatch.core import get_agent
        storage = get_agent().storage

    if tables is None:
        tables = ["traces", "logs", "health", "costs", "metrics"]

    should_close = False
    f: TextIO
    if isinstance(output, str):
        f = open(output, "w")  # type: ignore[assignment]
        should_close = True
    else:
        f = output

    try:
        count = 0
        agent_where = " AND agent_name = ?" if agent_name else ""
        agent_params = [agent_name] if agent_name else []

        time_where = ""
        time_params: list[Any] = []
        if hours:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            time_where = " AND {ts_col} >= ?"
            time_params = [cutoff]

        with storage._connect() as conn:
            if "traces" in tables:
                tw = time_where.format(ts_col="started_at")
                rows = conn.execute(
                    f"SELECT * FROM traces WHERE 1=1{agent_where}{tw} ORDER BY started_at",
                    agent_params + time_params,
                ).fetchall()
                for row in rows:
                    d = dict(row)
                    d["metadata"] = json.loads(d.get("metadata") or "{}")
                    d["_type"] = "trace"
                    f.write(json.dumps(d) + "\n")
                    count += 1

                    # Include spans for each trace
                    spans = conn.execute(
                        "SELECT * FROM spans WHERE trace_id = ? ORDER BY started_at",
                        (d["id"],),
                    ).fetchall()
                    for span in spans:
                        sd = dict(span)
                        sd["metadata"] = json.loads(sd.get("metadata") or "{}")
                        sd["_type"] = "span"
                        f.write(json.dumps(sd) + "\n")
                        count += 1

            if "logs" in tables:
                tw = time_where.format(ts_col="timestamp")
                rows = conn.execute(
                    f"SELECT * FROM logs WHERE 1=1{agent_where}{tw} ORDER BY timestamp",
                    agent_params + time_params,
                ).fetchall()
                for row in rows:
                    d = dict(row)
                    d["metadata"] = json.loads(d.get("metadata") or "{}")
                    d["_type"] = "log"
                    f.write(json.dumps(d) + "\n")
                    count += 1

            if "health" in tables:
                tw = time_where.format(ts_col="timestamp")
                rows = conn.execute(
                    f"SELECT * FROM health_checks WHERE 1=1{agent_where}{tw} ORDER BY timestamp",
                    agent_params + time_params,
                ).fetchall()
                for row in rows:
                    d = dict(row)
                    d["metadata"] = json.loads(d.get("metadata") or "{}")
                    d["_type"] = "health_check"
                    f.write(json.dumps(d) + "\n")
                    count += 1

            if "costs" in tables:
                tw = time_where.format(ts_col="timestamp")
                rows = conn.execute(
                    f"SELECT * FROM token_usage WHERE 1=1{agent_where}{tw} ORDER BY timestamp",
                    agent_params + time_params,
                ).fetchall()
                for row in rows:
                    d = dict(row)
                    d["metadata"] = json.loads(d.get("metadata") or "{}")
                    d["_type"] = "token_usage"
                    f.write(json.dumps(d) + "\n")
                    count += 1

            if "metrics" in tables:
                tw = time_where.format(ts_col="timestamp")
                rows = conn.execute(
                    f"SELECT * FROM metrics WHERE 1=1{agent_where}{tw} ORDER BY timestamp",
                    agent_params + time_params,
                ).fetchall()
                for row in rows:
                    d = dict(row)
                    d["tags"] = json.loads(d.get("tags") or "{}")
                    d["_type"] = "metric"
                    f.write(json.dumps(d) + "\n")
                    count += 1

        return count
    finally:
        if should_close:
            f.close()


def _get_file_size(path: str) -> int:
    """Get file size in bytes, including WAL and SHM files."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        p = Path(path + suffix)
        if p.exists():
            total += p.stat().st_size
    return total
