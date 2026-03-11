"""
Prometheus/OpenMetrics exporter for AgentWatch.

Exposes agent metrics at a /metrics endpoint in standard Prometheus
text format. Designed to be scraped by Prometheus, VictoriaMetrics,
or any OpenMetrics-compatible collector.

Usage with the built-in server:

    agentwatch serve --metrics           # /metrics on same port
    agentwatch serve --metrics-port 9090 # /metrics on separate port

Programmatic usage:

    from agentwatch.exporters.prometheus import PrometheusExporter

    exporter = PrometheusExporter(storage)
    metrics_text = exporter.collect()

Metrics exposed:
    agentwatch_traces_total{agent, status}          - Total traces by status
    agentwatch_trace_duration_seconds{agent}         - Trace duration histogram
    agentwatch_logs_total{agent, level}              - Total logs by level
    agentwatch_health_status{agent, check}           - Health check status (0/1)
    agentwatch_health_duration_seconds{agent, check} - Health check duration
    agentwatch_tokens_total{agent, model, direction} - Token usage totals
    agentwatch_cost_usd_total{agent, model}          - Estimated cost in USD
    agentwatch_errors_total{agent}                   - Total error count
    agentwatch_agent_info{agent}                     - Agent info gauge (always 1)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agentwatch.storage import Storage


# Prometheus exposition format helpers

def _escape_label(value: str) -> str:
    """Escape a label value for Prometheus text format."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _metric_line(
    name: str,
    value: float | int,
    labels: dict[str, str] | None = None,
    timestamp_ms: int | None = None,
) -> str:
    """Format a single Prometheus metric line."""
    if labels:
        label_str = ",".join(
            f'{k}="{_escape_label(v)}"' for k, v in sorted(labels.items())
        )
        line = f"{name}{{{label_str}}} {value}"
    else:
        line = f"{name} {value}"

    if timestamp_ms is not None:
        line += f" {timestamp_ms}"

    return line


def _type_line(name: str, metric_type: str) -> str:
    """Format a TYPE declaration."""
    return f"# TYPE {name} {metric_type}"


def _help_line(name: str, help_text: str) -> str:
    """Format a HELP declaration."""
    return f"# HELP {name} {help_text}"


class PrometheusExporter:
    """
    Collects AgentWatch metrics and formats them for Prometheus scraping.

    Thread-safe — each collect() call queries storage independently.
    """

    def __init__(self, storage: Storage):
        self.storage = storage

    def collect(self) -> str:
        """
        Collect all metrics and return Prometheus text exposition format.

        Returns:
            Multi-line string in Prometheus text format.
        """
        lines: list[str] = []

        lines.extend(self._collect_trace_metrics())
        lines.extend(self._collect_log_metrics())
        lines.extend(self._collect_health_metrics())
        lines.extend(self._collect_cost_metrics())
        lines.extend(self._collect_custom_metrics())
        lines.extend(self._collect_agent_info())

        # EOF marker for OpenMetrics
        lines.append("")

        return "\n".join(lines)

    def _collect_trace_metrics(self) -> list[str]:
        """Collect trace-related metrics."""
        lines: list[str] = []
        stats = self.storage.get_stats()
        agents = stats.get("agents", [])

        # traces_total by agent and status
        lines.append(_help_line("agentwatch_traces_total", "Total number of traces"))
        lines.append(_type_line("agentwatch_traces_total", "counter"))

        for agent in agents:
            agent_stats = self.storage.get_stats(agent_name=agent)
            breakdown = agent_stats.get("trace_status_breakdown", {})
            for status, count in breakdown.items():
                lines.append(_metric_line(
                    "agentwatch_traces_total",
                    count,
                    {"agent": agent, "status": status},
                ))

        # trace duration (recent traces as a gauge of average)
        lines.append("")
        lines.append(_help_line(
            "agentwatch_trace_duration_seconds_avg",
            "Average trace duration in seconds (last 100 traces)",
        ))
        lines.append(_type_line("agentwatch_trace_duration_seconds_avg", "gauge"))

        for agent in agents:
            traces = self.storage.get_traces(agent_name=agent, limit=100)
            durations = [t["duration_ms"] for t in traces if t.get("duration_ms")]
            if durations:
                avg_sec = (sum(durations) / len(durations)) / 1000.0
                lines.append(_metric_line(
                    "agentwatch_trace_duration_seconds_avg",
                    round(avg_sec, 4),
                    {"agent": agent},
                ))

        # error rate gauge
        lines.append("")
        lines.append(_help_line(
            "agentwatch_error_rate_pct",
            "Error rate percentage (last 100 traces)",
        ))
        lines.append(_type_line("agentwatch_error_rate_pct", "gauge"))

        for agent in agents:
            agent_stats = self.storage.get_stats(agent_name=agent)
            lines.append(_metric_line(
                "agentwatch_error_rate_pct",
                agent_stats["recent_error_rate_pct"],
                {"agent": agent},
            ))

        lines.append("")
        return lines

    def _collect_log_metrics(self) -> list[str]:
        """Collect log-related metrics."""
        lines: list[str] = []
        stats = self.storage.get_stats()
        agents = stats.get("agents", [])

        lines.append(_help_line("agentwatch_logs_total", "Total number of log entries"))
        lines.append(_type_line("agentwatch_logs_total", "counter"))

        for agent in agents:
            for level in ("debug", "info", "warn", "error", "critical"):
                from agentwatch.models import LogLevel
                logs = self.storage.get_logs(
                    agent_name=agent,
                    level=LogLevel(level),
                    limit=0,
                )
                # We need a count query — get_logs returns rows, not counts.
                # Use a direct count query for efficiency.
                count = self._count_logs(agent, level)
                if count > 0:
                    lines.append(_metric_line(
                        "agentwatch_logs_total",
                        count,
                        {"agent": agent, "level": level},
                    ))

        lines.append("")
        return lines

    def _collect_health_metrics(self) -> list[str]:
        """Collect health check metrics."""
        lines: list[str] = []
        health = self.storage.get_health_latest()

        if not health:
            return lines

        # Health status as a numeric gauge
        # 0 = critical, 0.5 = warn, 1 = ok, -1 = unknown
        STATUS_VALUES = {"ok": 1, "warn": 0.5, "critical": 0, "unknown": -1}

        lines.append(_help_line(
            "agentwatch_health_status",
            "Health check status (1=ok, 0.5=warn, 0=critical, -1=unknown)",
        ))
        lines.append(_type_line("agentwatch_health_status", "gauge"))

        for h in health:
            val = STATUS_VALUES.get(h["status"], -1)
            lines.append(_metric_line(
                "agentwatch_health_status",
                val,
                {"agent": h["agent_name"], "check": h["name"]},
            ))

        # Health check duration
        lines.append("")
        lines.append(_help_line(
            "agentwatch_health_duration_seconds",
            "Health check execution duration in seconds",
        ))
        lines.append(_type_line("agentwatch_health_duration_seconds", "gauge"))

        for h in health:
            if h.get("duration_ms") is not None:
                lines.append(_metric_line(
                    "agentwatch_health_duration_seconds",
                    round(h["duration_ms"] / 1000.0, 4),
                    {"agent": h["agent_name"], "check": h["name"]},
                ))

        lines.append("")
        return lines

    def _collect_cost_metrics(self) -> list[str]:
        """Collect cost/token metrics."""
        lines: list[str] = []
        stats = self.storage.get_stats()
        agents = stats.get("agents", [])

        # Token totals by model and direction
        lines.append(_help_line(
            "agentwatch_tokens_total",
            "Total token usage",
        ))
        lines.append(_type_line("agentwatch_tokens_total", "counter"))

        lines.append("")
        lines.append(_help_line(
            "agentwatch_cost_usd_total",
            "Total estimated cost in USD",
        ))
        lines.append(_type_line("agentwatch_cost_usd_total", "counter"))

        for agent in agents:
            summary = self.storage.get_cost_summary(agent_name=agent)
            for model_data in summary.get("by_model", []):
                model = model_data["model"]

                # Input tokens
                lines.append(_metric_line(
                    "agentwatch_tokens_total",
                    model_data.get("input_tokens", 0),
                    {"agent": agent, "model": model, "direction": "input"},
                ))

                # Output tokens
                lines.append(_metric_line(
                    "agentwatch_tokens_total",
                    model_data.get("output_tokens", 0),
                    {"agent": agent, "model": model, "direction": "output"},
                ))

                # Cost
                lines.append(_metric_line(
                    "agentwatch_cost_usd_total",
                    round(model_data.get("cost_usd", 0), 6),
                    {"agent": agent, "model": model},
                ))

        lines.append("")
        return lines

    def _collect_custom_metrics(self) -> list[str]:
        """Collect user-defined custom metrics."""
        lines: list[str] = []
        metric_list = self.storage.list_metrics()

        if not metric_list:
            return lines

        # Group metrics by name
        seen_names: set[str] = set()
        for m in metric_list:
            name = m["name"]
            if name in seen_names:
                continue
            seen_names.add(name)

            # Sanitise metric name for Prometheus (letters, digits, underscores)
            prom_name = "agentwatch_custom_" + "".join(
                c if c.isalnum() or c == "_" else "_" for c in name
            )
            kind = m.get("kind", "gauge")
            prom_type = "counter" if kind == "counter" else "gauge"

            lines.append(_help_line(prom_name, f"Custom metric: {name}"))
            lines.append(_type_line(prom_name, prom_type))

            # Get latest value per agent for this metric
            for entry in metric_list:
                if entry["name"] == name:
                    lines.append(_metric_line(
                        prom_name,
                        entry.get("latest_value", 0),
                        {"agent": entry["agent_name"]},
                    ))

            lines.append("")

        return lines

    def _collect_agent_info(self) -> list[str]:
        """Collect agent info metrics."""
        lines: list[str] = []
        stats = self.storage.get_stats()
        agents = stats.get("agents", [])

        lines.append(_help_line(
            "agentwatch_agent_info",
            "Agent information (always 1)",
        ))
        lines.append(_type_line("agentwatch_agent_info", "gauge"))

        for agent in agents:
            lines.append(_metric_line(
                "agentwatch_agent_info",
                1,
                {"agent": agent},
            ))

        return lines

    def _count_logs(self, agent_name: str, level: str) -> int:
        """Count logs efficiently with a direct SQL query."""
        with self.storage._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM logs WHERE agent_name = ? AND level = ?",
                (agent_name, level),
            ).fetchone()
            return row[0] if row else 0
