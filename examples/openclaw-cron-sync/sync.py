#!/usr/bin/env python3
"""
Sync OpenClaw cron run statuses to AgentWatch.

Polls `openclaw cron list --json` and `openclaw cron runs <id> --json`
to extract recent job outcomes, then POSTs them to the AgentWatch
ingestion endpoint.

This is an integration example — not part of the core AgentWatch library.
Any scheduler that can produce job name + status + duration can use the
same /api/v1/ingest/cron_run endpoint directly.

Usage:
    python3 sync.py

Environment variables:
    AGENTWATCH_URL   AgentWatch server URL (default: http://localhost:8470)
    AGENT_NAME       Agent name for records (default: openclaw)
    LOOKBACK_HOURS   How far back to look for runs (default: 1)
"""

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

AGENTWATCH_URL = os.environ.get("AGENTWATCH_URL", "http://localhost:8470")
AGENT_NAME = os.environ.get("AGENT_NAME", "openclaw")
INGEST_URL = f"{AGENTWATCH_URL}/api/v1/ingest/cron_run"
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "1"))


def run_openclaw(*args: str) -> list[dict]:
    """Run an openclaw CLI command and return parsed JSON output."""
    try:
        result = subprocess.run(
            ["openclaw", *args, "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(
                f"  ⚠ openclaw {' '.join(args)} failed: {result.stderr.strip()}",
                file=sys.stderr,
            )
            return []
        if not result.stdout.strip():
            return []
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        print(f"  ⚠ openclaw error: {e}", file=sys.stderr)
        return []


def post_records(records: list[dict]) -> int:
    """POST cron run records to AgentWatch. Returns number ingested."""
    if not records:
        return 0

    body = json.dumps(records).encode("utf-8")
    req = urllib.request.Request(
        INGEST_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("ingested", 0)
    except Exception as e:
        print(f"  ⚠ POST to AgentWatch failed: {e}", file=sys.stderr)
        return 0


def main() -> None:
    print(f"🔄 Syncing OpenClaw cron runs → {AGENTWATCH_URL}")
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    ).isoformat()

    jobs = run_openclaw("cron", "list")
    if not jobs:
        print("  No cron jobs found or openclaw not available.")
        return

    records = []
    for job in jobs:
        job_id = job.get("id") or job.get("jobId")
        job_name = job.get("name") or job_id or "unknown"

        if not job_id:
            continue

        runs = run_openclaw("cron", "runs", str(job_id))
        for run in runs:
            # Skip runs outside the lookback window
            ts = run.get("startedAt") or run.get("timestamp") or ""
            if ts and ts < cutoff:
                continue

            # Map openclaw run status to agentwatch status
            if run.get("success") is True:
                status = "ok"
            elif run.get("success") is False:
                status = "error"
            else:
                status = run.get("status", "unknown")

            error = run.get("error") or run.get("errorMessage")
            duration_ms = run.get("durationMs") or run.get("duration_ms")

            records.append(
                {
                    "job_name": job_name,
                    "status": status,
                    "duration_ms": duration_ms,
                    "error": str(error)[:500] if error else None,
                    "agent_name": AGENT_NAME,
                }
            )

    ingested = post_records(records)
    print(f"  ✅ Synced {ingested} run record(s) from {len(jobs)} job(s)")


if __name__ == "__main__":
    main()
