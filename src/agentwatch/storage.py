"""
SQLite storage backend for AgentWatch.

All data persists locally — no external dependencies. The schema is
designed for fast reads by agent name, time range, and status.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator

from agentwatch.models import (
    HealthCheck,
    HealthStatus,
    LogEntry,
    LogLevel,
    Span,
    SpanEvent,
    Trace,
    TraceStatus,
)

# Lazy import to avoid circular dependency
# MetricPoint is imported where needed in metric methods

DEFAULT_DB_PATH = os.path.expanduser("~/.agentwatch/agentwatch.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_ms REAL,
    metadata TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS spans (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_id TEXT,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_ms REAL,
    metadata TEXT DEFAULT '{}',
    error TEXT,
    FOREIGN KEY (trace_id) REFERENCES traces(id)
);

CREATE TABLE IF NOT EXISTS span_events (
    id TEXT PRIMARY KEY,
    span_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    message TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    FOREIGN KEY (span_id) REFERENCES spans(id)
);

CREATE TABLE IF NOT EXISTS logs (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    trace_id TEXT,
    span_id TEXT
);

CREATE TABLE IF NOT EXISTS health_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT DEFAULT '',
    timestamp TEXT NOT NULL,
    duration_ms REAL,
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_traces_agent ON traces(agent_name);
CREATE INDEX IF NOT EXISTS idx_traces_started ON traces(started_at);
CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_logs_agent ON logs(agent_name);
CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level);
CREATE INDEX IF NOT EXISTS idx_health_agent ON health_checks(agent_name);
CREATE INDEX IF NOT EXISTS idx_health_name ON health_checks(name);
CREATE INDEX IF NOT EXISTS idx_health_timestamp ON health_checks(timestamp);

CREATE TABLE IF NOT EXISTS token_usage (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    timestamp TEXT NOT NULL,
    trace_id TEXT,
    span_id TEXT,
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_token_agent ON token_usage(agent_name);
CREATE INDEX IF NOT EXISTS idx_token_model ON token_usage(model);
CREATE INDEX IF NOT EXISTS idx_token_timestamp ON token_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_token_trace ON token_usage(trace_id);

CREATE TABLE IF NOT EXISTS metrics (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    kind TEXT NOT NULL DEFAULT 'gauge',
    tags TEXT DEFAULT '{}',
    timestamp TEXT NOT NULL,
    trace_id TEXT,
    span_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_metrics_agent ON metrics(agent_name);
CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name);
CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_metrics_name_agent ON metrics(name, agent_name);

CREATE TABLE IF NOT EXISTS model_usage (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    latency_ms REAL,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_model_usage_agent ON model_usage(agent_name);
CREATE INDEX IF NOT EXISTS idx_model_usage_model ON model_usage(model);
CREATE INDEX IF NOT EXISTS idx_model_usage_timestamp ON model_usage(timestamp);

CREATE TABLE IF NOT EXISTS cron_runs (
    id TEXT PRIMARY KEY,
    agent_name TEXT,
    job_name TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms REAL,
    error TEXT,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cron_runs_job ON cron_runs(job_name);
CREATE INDEX IF NOT EXISTS idx_cron_runs_timestamp ON cron_runs(timestamp);
CREATE INDEX IF NOT EXISTS idx_cron_runs_status ON cron_runs(status);
"""


def _percentile(values: list[float], pct: int) -> float | None:
    """Compute a percentile from a sorted list of floats."""
    if not values:
        return None
    k = (len(values) - 1) * pct / 100
    f = int(k)
    c = min(f + 1, len(values) - 1)
    return round(values[f] + (values[c] - values[f]) * (k - f), 1)


class Storage:
    """Thread-safe SQLite storage for AgentWatch data."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._local = threading.local()

        # Ensure directory exists
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        # Initialise schema on the creating thread
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a thread-local connection with WAL mode for concurrent reads."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn

        try:
            yield self._local.conn
            self._local.conn.commit()
        except Exception:
            self._local.conn.rollback()
            raise

    def close(self) -> None:
        """Close the thread-local connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Run any schema migrations for existing databases."""
        # Check if metrics table exists (added in v0.2.0)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        # CREATE TABLE IF NOT EXISTS handles new tables automatically
        # This method is for future column additions or data migrations

    # ─── Traces ──────────────────────────────────────────────────────────

    def save_trace(self, trace: Trace) -> None:
        """Insert or update a trace and its root span."""
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO traces
                   (id, agent_name, name, status, started_at, ended_at, duration_ms, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trace.id,
                    trace.agent_name,
                    trace.name,
                    trace.status.value,
                    trace.started_at.isoformat(),
                    trace.ended_at.isoformat() if trace.ended_at else None,
                    trace.duration_ms,
                    json.dumps(trace.metadata),
                ),
            )
            if trace.root_span:
                self.save_span(trace.root_span, _conn=conn)

    def save_span(self, span: Span, _conn: sqlite3.Connection | None = None) -> None:
        """Insert or update a span and its events."""
        def _do(conn: sqlite3.Connection) -> None:
            conn.execute(
                """INSERT OR REPLACE INTO spans
                   (id, trace_id, parent_id, name, status, started_at, ended_at, duration_ms, metadata, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    span.id,
                    span.trace_id,
                    span.parent_id,
                    span.name,
                    span.status.value,
                    span.started_at.isoformat(),
                    span.ended_at.isoformat() if span.ended_at else None,
                    span.duration_ms,
                    json.dumps(span.metadata),
                    span.error,
                ),
            )
            for evt in span.events:
                conn.execute(
                    """INSERT OR REPLACE INTO span_events
                       (id, span_id, timestamp, message, metadata)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        evt.id,
                        evt.span_id,
                        evt.timestamp.isoformat(),
                        evt.message,
                        json.dumps(evt.metadata),
                    ),
                )

        if _conn:
            _do(_conn)
        else:
            with self._connect() as conn:
                _do(conn)

    def get_traces(
        self,
        agent_name: str | None = None,
        status: TraceStatus | None = None,
        name_contains: str | None = None,
        min_duration_ms: float | None = None,
        max_duration_ms: float | None = None,
        hours: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Query traces with optional filters.

        Args:
            agent_name: Filter by agent name.
            status: Filter by trace status.
            name_contains: Search trace names (case-insensitive substring match).
            min_duration_ms: Minimum duration in milliseconds.
            max_duration_ms: Maximum duration in milliseconds.
            hours: Only include traces from the last N hours.
            limit: Maximum number of results.
            offset: Offset for pagination.
        """
        with self._connect() as conn:
            query = "SELECT * FROM traces WHERE 1=1"
            params: list[Any] = []

            if agent_name:
                query += " AND agent_name = ?"
                params.append(agent_name)
            if status:
                query += " AND status = ?"
                params.append(status.value)
            if name_contains:
                query += " AND name LIKE ?"
                params.append(f"%{name_contains}%")
            if min_duration_ms is not None:
                query += " AND duration_ms >= ?"
                params.append(min_duration_ms)
            if max_duration_ms is not None:
                query += " AND duration_ms <= ?"
                params.append(max_duration_ms)
            if hours is not None:
                from datetime import datetime, timedelta, timezone
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
                query += " AND started_at >= ?"
                params.append(cutoff)

            query += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        """Get a single trace with its spans and events."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM traces WHERE id = ?", (trace_id,)).fetchone()
            if not row:
                return None

            trace = dict(row)
            trace["metadata"] = json.loads(trace.get("metadata") or "{}")

            spans = conn.execute(
                "SELECT * FROM spans WHERE trace_id = ? ORDER BY started_at",
                (trace_id,),
            ).fetchall()

            trace["spans"] = []
            for span_row in spans:
                span = dict(span_row)
                span["metadata"] = json.loads(span.get("metadata") or "{}")
                events = conn.execute(
                    "SELECT * FROM span_events WHERE span_id = ? ORDER BY timestamp",
                    (span["id"],),
                ).fetchall()
                span["events"] = [dict(e) for e in events]
                for evt in span["events"]:
                    evt["metadata"] = json.loads(evt.get("metadata") or "{}")
                trace["spans"].append(span)

            return trace

    # ─── Logs ────────────────────────────────────────────────────────────

    def save_log(self, entry: LogEntry) -> None:
        """Insert a log entry."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO logs
                   (id, agent_name, level, message, timestamp, metadata, trace_id, span_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.id,
                    entry.agent_name,
                    entry.level.value,
                    entry.message,
                    entry.timestamp.isoformat(),
                    json.dumps(entry.metadata),
                    entry.trace_id,
                    entry.span_id,
                ),
            )

    def get_logs(
        self,
        agent_name: str | None = None,
        level: LogLevel | None = None,
        search: str | None = None,
        hours: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query logs with optional filters.

        Args:
            agent_name: Filter by agent name.
            level: Filter by log level.
            search: Search log messages (case-insensitive substring match).
            hours: Only include logs from the last N hours.
            limit: Maximum number of results.
            offset: Offset for pagination.
        """
        with self._connect() as conn:
            query = "SELECT * FROM logs WHERE 1=1"
            params: list[Any] = []

            if agent_name:
                query += " AND agent_name = ?"
                params.append(agent_name)
            if level:
                query += " AND level = ?"
                params.append(level.value)
            if search:
                query += " AND message LIKE ?"
                params.append(f"%{search}%")
            if hours is not None:
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
                query += " AND timestamp >= ?"
                params.append(cutoff)

            query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()
            results = [dict(r) for r in rows]
            for r in results:
                r["metadata"] = json.loads(r.get("metadata") or "{}")
            return results

    # ─── Health Checks ───────────────────────────────────────────────────

    def save_health_check(self, check: HealthCheck) -> None:
        """Insert a health check result."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO health_checks
                   (name, agent_name, status, message, timestamp, duration_ms, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    check.name,
                    check.agent_name,
                    check.status.value,
                    check.message,
                    check.timestamp.isoformat(),
                    check.duration_ms,
                    json.dumps(check.metadata),
                ),
            )

    def get_health_latest(self, agent_name: str | None = None) -> list[dict[str, Any]]:
        """Get the latest health check result for each check name."""
        with self._connect() as conn:
            if agent_name:
                query = """
                    SELECT h.* FROM health_checks h
                    INNER JOIN (
                        SELECT name, MAX(timestamp) as max_ts
                        FROM health_checks WHERE agent_name = ?
                        GROUP BY name
                    ) latest ON h.name = latest.name AND h.timestamp = latest.max_ts
                    WHERE h.agent_name = ?
                    ORDER BY h.name
                """
                rows = conn.execute(query, (agent_name, agent_name)).fetchall()
            else:
                query = """
                    SELECT h.* FROM health_checks h
                    INNER JOIN (
                        SELECT name, agent_name, MAX(timestamp) as max_ts
                        FROM health_checks
                        GROUP BY name, agent_name
                    ) latest ON h.name = latest.name AND h.agent_name = latest.agent_name
                        AND h.timestamp = latest.max_ts
                    ORDER BY h.agent_name, h.name
                """
                rows = conn.execute(query).fetchall()

            results = [dict(r) for r in rows]
            for r in results:
                r["metadata"] = json.loads(r.get("metadata") or "{}")
            return results

    def get_health_history(
        self,
        name: str,
        agent_name: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get historical health check results for a named check."""
        with self._connect() as conn:
            query = "SELECT * FROM health_checks WHERE name = ?"
            params: list[Any] = [name]

            if agent_name:
                query += " AND agent_name = ?"
                params.append(agent_name)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            results = [dict(r) for r in rows]
            for r in results:
                r["metadata"] = json.loads(r.get("metadata") or "{}")
            return results

    # ─── Token Usage / Costs ────────────────────────────────────────────

    def save_token_usage(self, usage: Any) -> None:
        """Insert a token usage record."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO token_usage
                   (id, agent_name, model, input_tokens, output_tokens, total_tokens,
                    estimated_cost_usd, timestamp, trace_id, span_id, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    usage.id,
                    usage.agent_name,
                    usage.model,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.total_tokens,
                    usage.estimated_cost_usd,
                    usage.timestamp.isoformat(),
                    usage.trace_id,
                    usage.span_id,
                    json.dumps(usage.metadata),
                ),
            )

    def get_token_usage(
        self,
        agent_name: str | None = None,
        model: str | None = None,
        hours: int | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Query token usage records with optional filters."""
        with self._connect() as conn:
            query = "SELECT * FROM token_usage WHERE 1=1"
            params: list[Any] = []

            if agent_name:
                query += " AND agent_name = ?"
                params.append(agent_name)
            if model:
                query += " AND model = ?"
                params.append(model)
            if hours:
                from datetime import datetime, timedelta, timezone
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
                query += " AND timestamp >= ?"
                params.append(cutoff)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            results = [dict(r) for r in rows]
            for r in results:
                r["metadata"] = json.loads(r.get("metadata") or "{}")
            return results

    def get_cost_summary(self, agent_name: str | None = None, hours: int | None = None) -> dict[str, Any]:
        """Get aggregated cost summary."""
        with self._connect() as conn:
            where_parts = ["1=1"]
            params: list[Any] = []

            if agent_name:
                where_parts.append("agent_name = ?")
                params.append(agent_name)
            if hours:
                from datetime import datetime, timedelta, timezone
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
                where_parts.append("timestamp >= ?")
                params.append(cutoff)

            where = " AND ".join(where_parts)

            row = conn.execute(
                f"""SELECT
                    COUNT(*) as record_count,
                    COALESCE(SUM(input_tokens), 0) as total_input,
                    COALESCE(SUM(output_tokens), 0) as total_output,
                    COALESCE(SUM(total_tokens), 0) as total_tokens,
                    COALESCE(SUM(estimated_cost_usd), 0) as total_cost
                FROM token_usage WHERE {where}""",
                params,
            ).fetchone()

            by_model = conn.execute(
                f"""SELECT model,
                    COUNT(*) as count,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    SUM(estimated_cost_usd) as cost_usd
                FROM token_usage WHERE {where}
                GROUP BY model ORDER BY cost_usd DESC""",
                params,
            ).fetchall()

            return {
                "record_count": row["record_count"],
                "total_input_tokens": row["total_input"],
                "total_output_tokens": row["total_output"],
                "total_tokens": row["total_tokens"],
                "total_cost_usd": round(row["total_cost"], 4),
                "by_model": [dict(r) for r in by_model],
            }

    # ─── Stats ───────────────────────────────────────────────────────────

    def get_stats(self, agent_name: str | None = None) -> dict[str, Any]:
        """Get aggregate statistics."""
        with self._connect() as conn:
            where = "WHERE agent_name = ?" if agent_name else ""
            params = [agent_name] if agent_name else []

            trace_count = conn.execute(
                f"SELECT COUNT(*) FROM traces {where}", params
            ).fetchone()[0]

            log_count = conn.execute(
                f"SELECT COUNT(*) FROM logs {where}", params
            ).fetchone()[0]

            health_count = conn.execute(
                f"SELECT COUNT(*) FROM health_checks {where}", params
            ).fetchone()[0]

            # Trace status breakdown
            status_rows = conn.execute(
                f"SELECT status, COUNT(*) as cnt FROM traces {where} GROUP BY status",
                params,
            ).fetchall()
            status_breakdown = {r["status"]: r["cnt"] for r in status_rows}

            # Error rate (last 100 traces)
            recent = conn.execute(
                f"SELECT status FROM traces {where} ORDER BY started_at DESC LIMIT 100",
                params,
            ).fetchall()
            failed = sum(1 for r in recent if r["status"] == "failed")
            error_rate = (failed / len(recent) * 100) if recent else 0

            # Agent names
            agents = conn.execute(
                "SELECT DISTINCT agent_name FROM traces"
            ).fetchall()

            # Metric count
            metric_count = conn.execute(
                f"SELECT COUNT(*) FROM metrics {where}", params
            ).fetchone()[0]

            return {
                "total_traces": trace_count,
                "total_logs": log_count,
                "total_health_checks": health_count,
                "total_metrics": metric_count,
                "trace_status_breakdown": status_breakdown,
                "recent_error_rate_pct": round(error_rate, 1),
                "agents": [r["agent_name"] for r in agents],
            }

    # ─── Custom Metrics ──────────────────────────────────────────────────

    def save_metric(self, point: Any) -> None:
        """Insert a metric data point."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO metrics
                   (id, agent_name, name, value, kind, tags, timestamp, trace_id, span_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    point.id,
                    point.agent_name,
                    point.name,
                    point.value,
                    point.kind,
                    json.dumps(point.tags),
                    point.timestamp.isoformat(),
                    point.trace_id,
                    point.span_id,
                ),
            )

    def get_metrics(
        self,
        name: str | None = None,
        agent_name: str | None = None,
        tags: dict[str, str] | None = None,
        hours: int | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Query metric data points with optional filters."""
        with self._connect() as conn:
            query = "SELECT * FROM metrics WHERE 1=1"
            params: list[Any] = []

            if name:
                query += " AND name = ?"
                params.append(name)
            if agent_name:
                query += " AND agent_name = ?"
                params.append(agent_name)
            if hours:
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
                query += " AND timestamp >= ?"
                params.append(cutoff)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            results = [dict(r) for r in rows]
            for r in results:
                r["tags"] = json.loads(r.get("tags") or "{}")

            # Post-filter by tags if requested
            if tags:
                filtered = []
                for r in results:
                    match = all(r["tags"].get(k) == v for k, v in tags.items())
                    if match:
                        filtered.append(r)
                results = filtered

            return results

    def get_metric_summary(
        self,
        name: str,
        agent_name: str | None = None,
        tags: dict[str, str] | None = None,
        hours: int | None = None,
    ) -> dict[str, Any]:
        """Get aggregate statistics for a metric."""
        with self._connect() as conn:
            where_parts = ["name = ?"]
            params: list[Any] = [name]

            if agent_name:
                where_parts.append("agent_name = ?")
                params.append(agent_name)
            if hours:
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
                where_parts.append("timestamp >= ?")
                params.append(cutoff)

            where = " AND ".join(where_parts)

            row = conn.execute(
                f"""SELECT
                    COUNT(*) as count,
                    MIN(value) as min_value,
                    MAX(value) as max_value,
                    AVG(value) as avg_value,
                    SUM(value) as sum_value
                FROM metrics WHERE {where}""",
                params,
            ).fetchone()

            # Get latest value
            latest_row = conn.execute(
                f"SELECT value, timestamp FROM metrics WHERE {where} ORDER BY timestamp DESC LIMIT 1",
                params,
            ).fetchone()

            # Get time series for sparkline (last 50 points)
            series_rows = conn.execute(
                f"SELECT value, timestamp FROM metrics WHERE {where} ORDER BY timestamp ASC LIMIT 50",
                params,
            ).fetchall()

            result: dict[str, Any] = {
                "name": name,
                "count": row["count"],
                "min": round(row["min_value"], 4) if row["min_value"] is not None else None,
                "max": round(row["max_value"], 4) if row["max_value"] is not None else None,
                "avg": round(row["avg_value"], 4) if row["avg_value"] is not None else None,
                "sum": round(row["sum_value"], 4) if row["sum_value"] is not None else None,
                "latest_value": latest_row["value"] if latest_row else None,
                "latest_timestamp": latest_row["timestamp"] if latest_row else None,
                "series": [{"value": r["value"], "timestamp": r["timestamp"]} for r in series_rows],
            }
            return result

    def list_metrics(
        self,
        agent_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all known metric names with latest values and counts."""
        with self._connect() as conn:
            if agent_name:
                rows = conn.execute(
                    """SELECT name, kind, COUNT(*) as count,
                       (SELECT value FROM metrics m2
                        WHERE m2.name = metrics.name AND m2.agent_name = metrics.agent_name
                        ORDER BY timestamp DESC LIMIT 1) as latest_value,
                       agent_name
                    FROM metrics
                    WHERE agent_name = ?
                    GROUP BY name, agent_name
                    ORDER BY name""",
                    (agent_name,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT name, kind, COUNT(*) as count,
                       (SELECT value FROM metrics m2
                        WHERE m2.name = metrics.name AND m2.agent_name = metrics.agent_name
                        ORDER BY timestamp DESC LIMIT 1) as latest_value,
                       agent_name
                    FROM metrics
                    GROUP BY name, agent_name
                    ORDER BY name""",
                ).fetchall()

            return [dict(r) for r in rows]

    # ─── Model Usage ─────────────────────────────────────────────────────────

    def record_model_usage(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        latency_ms: float | None = None,
        agent_name: str = "unknown",
    ) -> str:
        """Record a single model invocation with token counts and cost."""
        import uuid
        record_id = uuid.uuid4().hex[:16]
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO model_usage
                   (id, agent_name, model, prompt_tokens, completion_tokens, cost_usd, latency_ms, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record_id,
                    agent_name,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    cost_usd,
                    latency_ms,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return record_id

    def get_model_stats(self, hours: int = 24) -> list[dict[str, Any]]:
        """Return per-model aggregate stats for the given time window.

        Each row: model, requests, prompt_tokens, completion_tokens,
        total_tokens, total_cost_usd, avg_latency_ms, p50_latency_ms,
        p95_latency_ms.
        """
        with self._connect() as conn:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=hours)
            ).isoformat()

            rows = conn.execute(
                """SELECT model,
                          COUNT(*) AS requests,
                          COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                          COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                          COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS total_tokens,
                          COALESCE(SUM(cost_usd), 0) AS total_cost_usd,
                          COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
                   FROM model_usage
                   WHERE timestamp >= ?
                   GROUP BY model
                   ORDER BY total_cost_usd DESC""",
                (cutoff,),
            ).fetchall()

            results = []
            for row in rows:
                r = dict(row)
                latencies = [
                    lr[0]
                    for lr in conn.execute(
                        "SELECT latency_ms FROM model_usage WHERE model = ? AND timestamp >= ? AND latency_ms IS NOT NULL ORDER BY latency_ms",
                        (r["model"], cutoff),
                    ).fetchall()
                ]
                r["p50_latency_ms"] = _percentile(latencies, 50)
                r["p95_latency_ms"] = _percentile(latencies, 95)
                r["total_cost_usd"] = round(r["total_cost_usd"], 6)
                r["avg_latency_ms"] = round(r["avg_latency_ms"], 1) if r["avg_latency_ms"] else None
                results.append(r)
            return results

    # ─── Cron Runs ───────────────────────────────────────────────────────────

    def record_cron_run(
        self,
        job_name: str,
        status: str,
        duration_ms: float | None = None,
        error: str | None = None,
        agent_name: str | None = None,
    ) -> str:
        """Record a cron job execution result."""
        import uuid
        record_id = uuid.uuid4().hex[:16]
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO cron_runs
                   (id, agent_name, job_name, status, duration_ms, error, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    record_id,
                    agent_name,
                    job_name,
                    status,
                    duration_ms,
                    error,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return record_id

    def get_cron_stats(self) -> list[dict[str, Any]]:
        """Return per-job stats: last run status, success rate, consecutive errors, avg duration."""
        with self._connect() as conn:
            jobs = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT job_name FROM cron_runs ORDER BY job_name"
                ).fetchall()
            ]

            results = []
            for job in jobs:
                rows = conn.execute(
                    """SELECT status, duration_ms, timestamp, error
                       FROM cron_runs WHERE job_name = ?
                       ORDER BY timestamp DESC LIMIT 100""",
                    (job,),
                ).fetchall()

                if not rows:
                    continue

                total = len(rows)
                ok_count = sum(1 for r in rows if r[0] == "ok")
                success_rate = round(ok_count / total * 100, 1) if total else 0

                consec_errors = 0
                for r in rows:
                    if r[0] != "ok":
                        consec_errors += 1
                    else:
                        break

                durations = [r[1] for r in rows if r[1] is not None]
                avg_dur = round(sum(durations) / len(durations), 1) if durations else None

                last = dict(rows[0])
                results.append({
                    "job_name": job,
                    "last_status": last["status"],
                    "last_run": last["timestamp"],
                    "last_error": last["error"],
                    "success_rate": success_rate,
                    "total_runs": total,
                    "consecutive_errors": consec_errors,
                    "avg_duration_ms": avg_dur,
                })

            return results

    def get_cron_history(self, job_name: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent run history for a specific cron job."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM cron_runs WHERE job_name = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (job_name, limit),
            ).fetchall()
            return [dict(r) for r in rows]
