"""
AgentWatch CLI — inspect and manage agent observability data.

Usage:
    agentwatch status                     Show overall status
    agentwatch traces [--agent NAME]      List recent traces
    agentwatch trace <ID>                 Show trace detail
    agentwatch logs [--agent NAME] [--level LEVEL]  Show recent logs
    agentwatch health [--agent NAME]      Show latest health checks
    agentwatch stats [--agent NAME]       Show aggregate statistics
    agentwatch costs [--agent NAME]       Show cost summary
    agentwatch patterns [--hours N]       Show detected patterns
    agentwatch report [--hours N]         Generate summary report
    agentwatch db info                    Show database statistics
    agentwatch db prune [--days N]        Prune old data
    agentwatch db vacuum                  Reclaim disk space
    agentwatch db export [-o FILE]        Export data to JSONL
    agentwatch tail [--traces]             Follow logs in real-time
    agentwatch serve [--port N]           Start web dashboard
    agentwatch version                    Show version
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from agentwatch.storage import Storage
from agentwatch.models import TraceStatus, LogLevel


def _get_storage(args: argparse.Namespace) -> Storage:
    """Create a Storage instance from CLI args."""
    return Storage(db_path=getattr(args, "db", None))


def _format_timestamp(ts: str | None) -> str:
    """Format an ISO timestamp for display."""
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return ts


def _format_duration(ms: float | None) -> str:
    """Format duration in milliseconds for display."""
    if ms is None:
        return "-"
    if ms < 1000:
        return f"{ms:.0f}ms"
    if ms < 60000:
        return f"{ms / 1000:.1f}s"
    return f"{ms / 60000:.1f}m"


STATUS_EMOJI = {"ok": "🟢", "warn": "🟡", "critical": "🔴", "unknown": "⚪"}
TRACE_EMOJI = {"running": "🔵", "completed": "✅", "failed": "❌"}
LEVEL_EMOJI = {"debug": "🔍", "info": "ℹ️ ", "warn": "⚠️ ", "error": "❌", "critical": "🔴"}


# ─── Commands ────────────────────────────────────────────────────────────────

def cmd_status(args: argparse.Namespace) -> None:
    """Show overall status."""
    storage = _get_storage(args)
    stats = storage.get_stats(agent_name=getattr(args, "agent", None))
    health = storage.get_health_latest(agent_name=getattr(args, "agent", None))

    # Overall health
    overall = "ok"
    for h in health:
        if h["status"] == "critical":
            overall = "critical"
            break
        if h["status"] == "warn":
            overall = "warn"

    emoji = STATUS_EMOJI.get(overall, "⚪")
    print(f"\n  {emoji} AgentWatch Status: {overall.upper()}\n")
    print(f"  Agents:  {', '.join(stats['agents']) or 'none'}")
    print(f"  Traces:  {stats['total_traces']}")
    print(f"  Logs:    {stats['total_logs']}")
    print(f"  Health:  {stats['total_health_checks']} checks recorded")
    if stats.get("total_metrics", 0) > 0:
        print(f"  Metrics: {stats['total_metrics']} data points")

    if stats["trace_status_breakdown"]:
        parts = []
        for s, c in stats["trace_status_breakdown"].items():
            parts.append(f"{TRACE_EMOJI.get(s, '?')} {s}: {c}")
        print(f"  Traces:  {' | '.join(parts)}")

    if stats["recent_error_rate_pct"] > 0:
        print(f"  Error rate (last 100): {stats['recent_error_rate_pct']}%")

    if health:
        print(f"\n  Health Checks:")
        for h in health:
            emoji = STATUS_EMOJI.get(h["status"], "⚪")
            print(f"    {emoji} {h['name']:<20} {h['status']:<10} {h.get('message', '')}")

    print()
    storage.close()


def cmd_traces(args: argparse.Namespace) -> None:
    """List recent traces."""
    storage = _get_storage(args)
    status_filter = TraceStatus(args.status) if getattr(args, "status", None) else None
    traces = storage.get_traces(
        agent_name=getattr(args, "agent", None),
        status=status_filter,
        name_contains=getattr(args, "search", None),
        min_duration_ms=getattr(args, "min_duration", None),
        hours=getattr(args, "hours", None),
        limit=getattr(args, "limit", 20),
    )

    if getattr(args, "json_output", False):
        print(json.dumps(traces, indent=2))
        storage.close()
        return

    if not traces:
        print("\n  No traces found.\n")
        storage.close()
        return

    print(f"\n  Recent Traces ({len(traces)}):\n")
    print(f"  {'ID':<18} {'Status':<12} {'Name':<30} {'Duration':<10} {'Started'}")
    print(f"  {'─' * 18} {'─' * 12} {'─' * 30} {'─' * 10} {'─' * 19}")

    for t in traces:
        emoji = TRACE_EMOJI.get(t["status"], "?")
        print(
            f"  {t['id']:<18} {emoji} {t['status']:<9} {t['name']:<30} "
            f"{_format_duration(t.get('duration_ms')):<10} {_format_timestamp(t['started_at'])}"
        )

    print()
    storage.close()


def cmd_trace_detail(args: argparse.Namespace) -> None:
    """Show detail for a single trace."""
    storage = _get_storage(args)
    trace = storage.get_trace(args.trace_id)

    if not trace:
        print(f"\n  Trace '{args.trace_id}' not found.\n")
        storage.close()
        return

    if getattr(args, "json_output", False):
        print(json.dumps(trace, indent=2))
        storage.close()
        return

    emoji = TRACE_EMOJI.get(trace["status"], "?")
    print(f"\n  {emoji} Trace: {trace['name']}")
    print(f"  ID:       {trace['id']}")
    print(f"  Agent:    {trace['agent_name']}")
    print(f"  Status:   {trace['status']}")
    print(f"  Started:  {_format_timestamp(trace['started_at'])}")
    print(f"  Ended:    {_format_timestamp(trace.get('ended_at'))}")
    print(f"  Duration: {_format_duration(trace.get('duration_ms'))}")

    if trace.get("metadata"):
        print(f"  Metadata: {json.dumps(trace['metadata'])}")

    spans = trace.get("spans", [])
    if spans:
        print(f"\n  Spans ({len(spans)}):\n")
        for s in spans:
            indent = "    "
            if s.get("parent_id"):
                indent = "      "
            span_emoji = TRACE_EMOJI.get(s["status"], "?")
            print(f"  {indent}{span_emoji} {s['name']} ({_format_duration(s.get('duration_ms'))})")
            if s.get("error"):
                print(f"  {indent}  ❌ {s['error']}")
            for evt in s.get("events", []):
                print(f"  {indent}  📝 {evt['message']}")

    print()
    storage.close()


def cmd_logs(args: argparse.Namespace) -> None:
    """Show recent logs."""
    storage = _get_storage(args)
    level_filter = None
    if getattr(args, "level", None):
        try:
            level_filter = LogLevel(args.level.lower())
        except ValueError:
            print(f"Unknown level: {args.level}")
            storage.close()
            return

    logs = storage.get_logs(
        agent_name=getattr(args, "agent", None),
        level=level_filter,
        search=getattr(args, "search", None),
        hours=getattr(args, "hours", None),
        limit=getattr(args, "limit", 50),
    )

    if getattr(args, "json_output", False):
        print(json.dumps(logs, indent=2))
        storage.close()
        return

    if not logs:
        print("\n  No logs found.\n")
        storage.close()
        return

    print(f"\n  Recent Logs ({len(logs)}):\n")
    for entry in logs:
        emoji = LEVEL_EMOJI.get(entry["level"], "?")
        ts = _format_timestamp(entry["timestamp"])
        agent = entry["agent_name"]
        msg = entry["message"]
        trace_ref = f" [trace:{entry['trace_id'][:8]}]" if entry.get("trace_id") else ""
        print(f"  {ts} {emoji} [{agent}] {msg}{trace_ref}")

    print()
    storage.close()


def cmd_health(args: argparse.Namespace) -> None:
    """Show latest health check results."""
    storage = _get_storage(args)
    results = storage.get_health_latest(agent_name=getattr(args, "agent", None))

    if getattr(args, "json_output", False):
        print(json.dumps(results, indent=2))
        storage.close()
        return

    if not results:
        print("\n  No health checks recorded.\n")
        storage.close()
        return

    overall = "ok"
    for r in results:
        if r["status"] == "critical":
            overall = "critical"
            break
        if r["status"] == "warn":
            overall = "warn"

    emoji = STATUS_EMOJI.get(overall, "⚪")
    print(f"\n  {emoji} Health Status: {overall.upper()}\n")

    for r in results:
        emoji = STATUS_EMOJI.get(r["status"], "⚪")
        duration = _format_duration(r.get("duration_ms"))
        print(f"  {emoji} {r['name']:<20} {r['status']:<10} {duration:<8} {r.get('message', '')}")
        if r.get("agent_name"):
            print(f"     Agent: {r['agent_name']}  |  Last: {_format_timestamp(r['timestamp'])}")

    print()
    storage.close()


def cmd_stats(args: argparse.Namespace) -> None:
    """Show aggregate statistics."""
    storage = _get_storage(args)
    stats = storage.get_stats(agent_name=getattr(args, "agent", None))

    if getattr(args, "json_output", False):
        print(json.dumps(stats, indent=2))
        storage.close()
        return

    print(f"\n  📊 AgentWatch Statistics\n")
    print(f"  Agents:        {', '.join(stats['agents']) or 'none'}")
    print(f"  Total traces:  {stats['total_traces']}")
    print(f"  Total logs:    {stats['total_logs']}")
    print(f"  Health checks: {stats['total_health_checks']}")

    if stats["trace_status_breakdown"]:
        print(f"\n  Trace Breakdown:")
        for s, c in stats["trace_status_breakdown"].items():
            emoji = TRACE_EMOJI.get(s, "?")
            print(f"    {emoji} {s}: {c}")

    print(f"\n  Error Rate (last 100): {stats['recent_error_rate_pct']}%")
    print()
    storage.close()


def cmd_costs(args: argparse.Namespace) -> None:
    """Show cost summary."""
    storage = _get_storage(args)
    summary = storage.get_cost_summary(
        agent_name=getattr(args, "agent", None),
        hours=getattr(args, "hours", None),
    )

    if getattr(args, "json_output", False):
        print(json.dumps(summary, indent=2))
        storage.close()
        return

    print(f"\n  💰 Cost Summary\n")
    print(f"  Total cost:     ${summary['total_cost_usd']:.4f}")
    print(f"  Input tokens:   {summary['total_input_tokens']:,}")
    print(f"  Output tokens:  {summary['total_output_tokens']:,}")
    print(f"  Total tokens:   {summary['total_tokens']:,}")
    print(f"  Records:        {summary['record_count']}")

    if summary["by_model"]:
        print(f"\n  By Model:")
        for m in summary["by_model"]:
            print(f"    {m['model']:<35} ${m['cost_usd']:.4f}  ({m['count']} calls)")

    print()
    storage.close()


def cmd_patterns(args: argparse.Namespace) -> None:
    """Show detected patterns."""
    from agentwatch.core import init, _reset, _agent
    from agentwatch.patterns import detect_patterns

    db_path = getattr(args, "db", None)
    was_init = _agent is not None
    if not was_init:
        init("_cli", db_path=db_path)

    try:
        patterns = detect_patterns(
            agent_name=getattr(args, "agent", None),
            window_hours=getattr(args, "hours", 24),
        )

        if getattr(args, "json_output", False):
            print(json.dumps([p.to_dict() for p in patterns], indent=2))
            return

        if not patterns:
            print("\n  ✅ No patterns detected. Systems look healthy.\n")
            return

        print(f"\n  🔍 Detected Patterns ({len(patterns)}):\n")
        SEVERITY_EMOJI = {"info": "ℹ️ ", "warn": "⚠️ ", "critical": "🔴"}
        for p in patterns:
            emoji = SEVERITY_EMOJI.get(p.severity.value, "?")
            print(f"  {emoji} [{p.severity.value.upper()}] {p.title}")
            print(f"     {p.description}")
            if p.occurrences > 1:
                print(f"     Occurrences: {p.occurrences}")
            print()
    finally:
        if not was_init:
            _reset()


def cmd_report(args: argparse.Namespace) -> None:
    """Generate a summary report."""
    from agentwatch.core import init, _reset, _agent

    db_path = getattr(args, "db", None)
    was_init = _agent is not None
    if not was_init:
        init("_cli", db_path=db_path)

    try:
        from agentwatch.reports import summary, summary_data

        hours = getattr(args, "hours", 24)
        agent = getattr(args, "agent", None)

        if getattr(args, "json_output", False):
            data = summary_data(hours=hours, agent_name=agent)
            print(json.dumps(data, indent=2))
        else:
            text = summary(hours=hours, agent_name=agent)
            print(text)
    finally:
        if not was_init:
            _reset()


def cmd_db(args: argparse.Namespace) -> None:
    """Database management commands."""
    subcmd = getattr(args, "db_command", None)
    if not subcmd:
        print("\n  Usage: agentwatch db {info|prune|vacuum|export}\n")
        return

    if subcmd == "info":
        cmd_db_info(args)
    elif subcmd == "prune":
        cmd_db_prune(args)
    elif subcmd == "vacuum":
        cmd_db_vacuum(args)
    elif subcmd == "export":
        cmd_db_export(args)


def cmd_db_info(args: argparse.Namespace) -> None:
    """Show database info."""
    from agentwatch.retention import db_info
    storage = _get_storage(args)

    info = db_info(storage=storage)

    if getattr(args, "json_output", False):
        print(json.dumps(info.to_dict(), indent=2))
        storage.close()
        return

    print(f"\n  📦 Database Info\n")
    print(f"  Path:     {info.path}")
    print(f"  Size:     {info.size_mb:.2f} MB ({info.size_bytes:,} bytes)")

    if info.oldest_trace:
        print(f"  Oldest:   {_format_timestamp(info.oldest_trace)}")
    if info.newest_trace:
        print(f"  Newest:   {_format_timestamp(info.newest_trace)}")

    print(f"\n  Table Counts:")
    for table, count in info.table_counts.items():
        print(f"    {table:<16} {count:>8,}")

    total = sum(info.table_counts.values())
    print(f"    {'─' * 16} {'─' * 8}")
    print(f"    {'total':<16} {total:>8,}")

    print()
    storage.close()


def cmd_db_prune(args: argparse.Namespace) -> None:
    """Prune old data."""
    from agentwatch.retention import prune
    storage = _get_storage(args)

    dry_run = getattr(args, "dry_run", False)
    days = getattr(args, "days", 30)

    if dry_run:
        print(f"\n  🔍 Dry run — showing what would be pruned (>{days} days):\n")
    else:
        print(f"\n  🗑️  Pruning data older than {days} days...\n")

    result = prune(
        days=days,
        agent_name=getattr(args, "agent", None),
        storage=storage,
        dry_run=dry_run,
    )

    if getattr(args, "json_output", False):
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"  {result.summary()}")

    print()
    storage.close()


def cmd_db_vacuum(args: argparse.Namespace) -> None:
    """Vacuum the database."""
    from agentwatch.retention import vacuum
    storage = _get_storage(args)

    print(f"\n  🧹 Running VACUUM...")
    saved = vacuum(storage=storage)

    if saved > 0:
        print(f"  Reclaimed {saved:,} bytes ({saved / 1024:.1f} KB)")
    else:
        print(f"  Database already compact.")

    print()
    storage.close()


def cmd_db_export(args: argparse.Namespace) -> None:
    """Export data to JSONL."""
    from agentwatch.retention import export_jsonl
    storage = _get_storage(args)

    output = getattr(args, "output", None) or "-"

    if output == "-":
        import sys as _sys
        count = export_jsonl(
            output=_sys.stdout,
            agent_name=getattr(args, "agent", None),
            hours=getattr(args, "hours", None),
            storage=storage,
        )
        # Count to stderr so it doesn't mix with JSONL output
        print(f"\n# Exported {count} records", file=_sys.stderr)
    else:
        count = export_jsonl(
            output=output,
            agent_name=getattr(args, "agent", None),
            hours=getattr(args, "hours", None),
            storage=storage,
        )
        print(f"\n  📤 Exported {count} records to {output}\n")

    storage.close()


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the web dashboard server."""
    from agentwatch.server.app import run_server
    auth_token = getattr(args, "auth_token", None)
    if not auth_token:
        import os
        auth_token = os.environ.get("AGENTWATCH_AUTH_TOKEN")
    run_server(
        db_path=getattr(args, "db", None),
        host=getattr(args, "host", "0.0.0.0"),
        port=getattr(args, "port", 8470),
        auth_token=auth_token,
    )


def cmd_tail(args: argparse.Namespace) -> None:
    """Follow logs and traces in real-time (like tail -f)."""
    import time

    storage = _get_storage(args)
    interval = getattr(args, "interval", 2.0)
    show_traces = getattr(args, "traces", False)
    level_filter = getattr(args, "level", None)
    agent_filter = getattr(args, "agent", None)

    # ANSI colours for log levels
    LEVEL_COLORS = {
        "debug": "\033[90m",    # grey
        "info": "\033[36m",     # cyan
        "warn": "\033[33m",     # yellow
        "warning": "\033[33m",  # yellow
        "error": "\033[31m",    # red
        "critical": "\033[91m", # bright red
    }
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    BLUE = "\033[34m"

    # Track what we've seen
    seen_log_ids: set[str] = set()
    seen_trace_ids: set[str] = set()

    # Load initial state (last 5 of each, just to show recent context)
    initial_logs = storage.get_logs(limit=5, agent_name=agent_filter)
    for log in reversed(initial_logs):
        seen_log_ids.add(log["id"])
    if show_traces:
        initial_traces = storage.get_traces(limit=5, agent_name=agent_filter)
        for t in reversed(initial_traces):
            seen_trace_ids.add(t["id"])

    print(f"{DIM}─── AgentWatch tail ({'traces + ' if show_traces else ''}logs) ───{RESET}")
    print(f"{DIM}Polling every {interval}s. Press Ctrl+C to stop.{RESET}\n")

    try:
        while True:
            # Check for new logs
            logs = storage.get_logs(limit=20, agent_name=agent_filter)
            new_logs = [l for l in reversed(logs) if l["id"] not in seen_log_ids]

            for log in new_logs:
                seen_log_ids.add(log["id"])
                lvl = log.get("level", "info").lower()
                if level_filter and lvl != level_filter.lower():
                    continue

                color = LEVEL_COLORS.get(lvl, "")
                ts = log.get("timestamp", "")[:19]
                agent = log.get("agent_name", "")
                msg = log.get("message", "")
                lvl_display = lvl.upper().ljust(8)

                print(f"{DIM}{ts}{RESET} {color}{lvl_display}{RESET} {DIM}[{agent}]{RESET} {msg}")

                # Show metadata if present
                meta = log.get("metadata")
                if meta:
                    for k, v in meta.items():
                        print(f"  {DIM}{k}: {v}{RESET}")

            # Check for new traces
            if show_traces:
                traces = storage.get_traces(limit=20, agent_name=agent_filter)
                new_traces = [t for t in reversed(traces) if t["id"] not in seen_trace_ids]

                for t in new_traces:
                    seen_trace_ids.add(t["id"])
                    status = t.get("status", "unknown")
                    name = t.get("name", "unnamed")
                    dur = t.get("duration_ms")
                    agent = t.get("agent_name", "")
                    ts = t.get("started_at", "")[:19]

                    if status == "completed":
                        icon = f"{GREEN}✓{RESET}"
                    elif status == "failed":
                        icon = f"{RED}✗{RESET}"
                    else:
                        icon = f"{BLUE}→{RESET}"

                    dur_str = f" {DIM}({dur:.0f}ms){RESET}" if dur else ""
                    print(f"{DIM}{ts}{RESET} {icon} {BOLD}TRACE{RESET}    {DIM}[{agent}]{RESET} {name}{dur_str}")

                    if status == "failed" and t.get("error"):
                        print(f"  {RED}error: {t['error']}{RESET}")

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n{DIM}─── tail stopped ───{RESET}")


def cmd_generate_token(args: argparse.Namespace) -> None:
    """Generate a secure random token for dashboard authentication."""
    from agentwatch.auth import generate_token
    token = generate_token()
    print(f"\n  🔑 Generated auth token:\n")
    print(f"     {token}\n")
    print(f"  Usage:")
    print(f"     agentwatch serve --auth-token \"{token}\"")
    print(f"     export AGENTWATCH_AUTH_TOKEN=\"{token}\"\n")


def cmd_init_config(args: argparse.Namespace) -> None:
    """Generate a starter agentwatch.toml config file."""
    from pathlib import Path

    path = Path(getattr(args, "output", "agentwatch.toml"))
    if path.exists() and not getattr(args, "force", False):
        print(f"  ⚠  {path} already exists. Use --force to overwrite.")
        sys.exit(1)

    config_content = '''# AgentWatch Configuration
# https://github.com/agentwatch/agentwatch

[agent]
# name = "my-agent"  # Override agent name

[server]
host = "0.0.0.0"
port = 8470
# auth_token = ""  # Set for dashboard authentication
# metrics = true   # Enable /metrics endpoint

[retention]
trace_days = 30
log_days = 14
health_days = 7
cost_days = 90

[alerts]
# cooldown_seconds = 300

# [costs.pricing]
# "my-fine-tuned-model" = [2.0, 8.0]  # [input $/1M, output $/1M]
'''
    path.write_text(config_content)
    print(f"  ✅ Created {path}")
    print(f"     Edit it, then run: agentwatch serve")


def cmd_metrics(args: argparse.Namespace) -> None:
    """Show custom metrics."""
    storage = _get_storage(args)
    json_out = getattr(args, "json_output", False)

    name = getattr(args, "name", None)
    agent = getattr(args, "agent", None)

    if name:
        # Show summary for a specific metric
        summary = storage.get_metric_summary(name, agent_name=agent)
        if json_out:
            print(json.dumps(summary, indent=2, default=str))
            return

        print(f"\n  📈 Metric: {name}")
        print(f"  {'─' * 40}")
        print(f"  Count:  {summary['count']}")
        print(f"  Latest: {summary['latest_value']}")
        print(f"  Min:    {summary['min']}")
        print(f"  Max:    {summary['max']}")
        print(f"  Avg:    {summary['avg']}")
        print(f"  Sum:    {summary['sum']}")
        print()
    else:
        # List all metrics
        metrics = storage.list_metrics(agent_name=agent)
        if json_out:
            print(json.dumps(metrics, indent=2, default=str))
            return

        if not metrics:
            print("\n  No custom metrics recorded.\n")
            return

        print(f"\n  📈 Custom Metrics ({len(metrics)} total)")
        print(f"  {'─' * 60}")

        # Header
        print(f"  {'Name':<30} {'Kind':<10} {'Latest':<12} {'Count':<8} {'Agent'}")
        print(f"  {'─' * 30} {'─' * 10} {'─' * 12} {'─' * 8} {'─' * 15}")

        for m in metrics:
            latest = m.get("latest_value")
            latest_str = f"{latest:.2f}" if latest is not None else "-"
            print(
                f"  {m['name']:<30} {m.get('kind', 'gauge'):<10} "
                f"{latest_str:<12} {m.get('count', 0):<8} {m.get('agent_name', '')}"
            )
        print()


def cmd_version(args: argparse.Namespace) -> None:
    """Show version."""
    from agentwatch import __version__
    print(f"agentwatch {__version__}")


# ─── Argument Parser ─────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentwatch",
        description="Lightweight observability for autonomous AI agents",
    )
    parser.add_argument("--db", help="Path to SQLite database", default=None)
    parser.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # status
    p_status = subparsers.add_parser("status", help="Show overall status")
    p_status.add_argument("--agent", help="Filter by agent name")

    # traces
    p_traces = subparsers.add_parser("traces", help="List recent traces")
    p_traces.add_argument("--agent", help="Filter by agent name")
    p_traces.add_argument("--status", help="Filter by status (running/completed/failed)")
    p_traces.add_argument("--search", "-s", help="Search trace names (substring match)")
    p_traces.add_argument("--hours", type=int, help="Only show traces from last N hours")
    p_traces.add_argument("--min-duration", type=float, help="Min duration in ms")
    p_traces.add_argument("--limit", type=int, default=20, help="Max results")

    # trace <id>
    p_trace = subparsers.add_parser("trace", help="Show trace detail")
    p_trace.add_argument("trace_id", help="Trace ID")

    # logs
    p_logs = subparsers.add_parser("logs", help="Show recent logs")
    p_logs.add_argument("--agent", help="Filter by agent name")
    p_logs.add_argument("--level", help="Filter by level (debug/info/warn/error/critical)")
    p_logs.add_argument("--search", "-s", help="Search log messages (substring match)")
    p_logs.add_argument("--hours", type=int, help="Only show logs from last N hours")
    p_logs.add_argument("--limit", type=int, default=50, help="Max results")

    # health
    p_health = subparsers.add_parser("health", help="Show health check results")
    p_health.add_argument("--agent", help="Filter by agent name")

    # stats
    p_stats = subparsers.add_parser("stats", help="Show aggregate statistics")
    p_stats.add_argument("--agent", help="Filter by agent name")

    # costs
    p_costs = subparsers.add_parser("costs", help="Show cost summary")
    p_costs.add_argument("--agent", help="Filter by agent name")
    p_costs.add_argument("--hours", type=int, help="Limit to last N hours")

    # patterns
    p_patterns = subparsers.add_parser("patterns", help="Show detected patterns")
    p_patterns.add_argument("--agent", help="Filter by agent name")
    p_patterns.add_argument("--hours", type=int, default=24, help="Window in hours (default: 24)")

    # report
    p_report = subparsers.add_parser("report", help="Generate summary report")
    p_report.add_argument("--agent", help="Filter by agent name")
    p_report.add_argument("--hours", type=int, default=24, help="Time window in hours (default: 24)")

    # db
    p_db = subparsers.add_parser("db", help="Database management")
    db_sub = p_db.add_subparsers(dest="db_command", help="Database command")

    db_info = db_sub.add_parser("info", help="Show database info")

    db_prune = db_sub.add_parser("prune", help="Prune old data")
    db_prune.add_argument("--days", type=int, default=30, help="Delete data older than N days (default: 30)")
    db_prune.add_argument("--agent", help="Only prune data for this agent")
    db_prune.add_argument("--dry-run", action="store_true", help="Show what would be pruned without deleting")

    db_vacuum = db_sub.add_parser("vacuum", help="Reclaim disk space")

    db_export = db_sub.add_parser("export", help="Export data to JSONL")
    db_export.add_argument("--output", "-o", help="Output file (default: stdout)")
    db_export.add_argument("--agent", help="Filter by agent name")
    db_export.add_argument("--hours", type=int, help="Limit to last N hours")

    # metrics
    p_metrics = subparsers.add_parser("metrics", help="Show custom metrics")
    p_metrics.add_argument("--agent", help="Filter by agent name")
    p_metrics.add_argument("--name", "-n", help="Show summary for a specific metric")

    # tail
    p_tail = subparsers.add_parser("tail", help="Follow logs/traces in real-time")
    p_tail.add_argument("--agent", help="Filter by agent name")
    p_tail.add_argument("--level", help="Filter by log level")
    p_tail.add_argument("--traces", action="store_true", help="Also show new traces")
    p_tail.add_argument("--interval", type=float, default=2.0, help="Poll interval in seconds (default: 2)")

    # serve
    p_serve = subparsers.add_parser("serve", help="Start web dashboard")
    p_serve.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    p_serve.add_argument("--port", type=int, default=8470, help="Port (default: 8470)")
    p_serve.add_argument("--auth-token", dest="auth_token", default=None,
                         help="Require this token for dashboard access (or set AGENTWATCH_AUTH_TOKEN)")

    # generate-token
    subparsers.add_parser("generate-token", help="Generate a secure auth token")

    # init
    p_init = subparsers.add_parser("init", help="Generate a starter config file")
    p_init.add_argument("--output", "-o", default="agentwatch.toml", help="Output path (default: agentwatch.toml)")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing file")

    # version
    subparsers.add_parser("version", help="Show version")

    return parser


COMMANDS = {
    "status": cmd_status,
    "traces": cmd_traces,
    "trace": cmd_trace_detail,
    "logs": cmd_logs,
    "health": cmd_health,
    "stats": cmd_stats,
    "costs": cmd_costs,
    "patterns": cmd_patterns,
    "report": cmd_report,
    "metrics": cmd_metrics,
    "db": cmd_db,
    "tail": cmd_tail,
    "serve": cmd_serve,
    "generate-token": cmd_generate_token,
    "init": cmd_init_config,
    "version": cmd_version,
}


def cli() -> None:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    cmd_fn = COMMANDS.get(args.command)
    if cmd_fn:
        cmd_fn(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    cli()
