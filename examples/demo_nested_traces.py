#!/usr/bin/env python3
"""
Demo with nested traces to show off the waterfall view.

    python3 examples/demo_nested_traces.py
    agentwatch serve
    # Open http://localhost:8470 and click on a trace
"""

import sys
import os
import time
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import agentwatch

agentwatch.init("pipeline-agent")

print("🌱 Creating nested traces for waterfall demo...\n")

# Pipeline 1: Email processing
with agentwatch.trace("email-pipeline") as pipeline:
    pipeline.event("Starting email processing pipeline")
    time.sleep(0.01)

    with agentwatch.trace("fetch-emails") as fetch:
        fetch.event("Connecting to IMAP server")
        time.sleep(0.02)
        fetch.event("Found 5 unread emails")
        fetch.set_metadata("email_count", 5)

    with agentwatch.trace("classify-emails") as classify:
        for i in range(3):
            time.sleep(0.005)
            classify.event(f"Classified email {i+1}: support-ticket")

    with agentwatch.trace("summarise-emails") as summarise:
        time.sleep(0.03)
        summarise.event("Generated summaries for 5 emails")
        agentwatch.costs.record(
            model="claude-sonnet-4-20250514",
            input_tokens=2500,
            output_tokens=800,
        )

    with agentwatch.trace("send-notifications") as notify:
        time.sleep(0.01)
        notify.event("Sent Slack notification")
        notify.event("Sent email digest")

    pipeline.event("Pipeline complete")

# Pipeline 2: Report generation (with a failure)
with agentwatch.trace("report-generation") as report:
    report.event("Starting weekly report generation")

    with agentwatch.trace("gather-metrics") as metrics:
        time.sleep(0.015)
        metrics.event("Collected 12 metrics from 3 sources")
        metrics.set_metadata("sources", ["grafana", "influxdb", "postgres"])

    with agentwatch.trace("generate-charts") as charts:
        time.sleep(0.04)
        charts.event("Generated 4 chart images")

    with agentwatch.trace("compose-report") as compose:
        time.sleep(0.025)
        agentwatch.costs.record(
            model="gpt-4o",
            input_tokens=3200,
            output_tokens=1500,
        )
        compose.event("Report composed (2,400 words)")

    with agentwatch.trace("deliver-report") as deliver:
        time.sleep(0.005)
        deliver.event("Saved to /reports/weekly-2026-03-06.pdf")
        deliver.event("Sent via email to 3 recipients")

# Pipeline 3: Health monitoring
with agentwatch.trace("health-sweep") as sweep:
    sweep.event("Running system health sweep")

    with agentwatch.trace("check-apis") as apis:
        time.sleep(0.008)
        apis.event("All 4 APIs responding")

    with agentwatch.trace("check-databases") as dbs:
        time.sleep(0.012)
        dbs.event("Primary: ok (3ms latency)")
        dbs.event("Replica: ok (5ms latency)")

    with agentwatch.trace("check-storage") as storage:
        time.sleep(0.005)
        storage.event("Disk usage: 62%")

    with agentwatch.trace("check-cron-jobs") as crons:
        time.sleep(0.003)
        crons.set_error("Cron 'daily-backup' missed last run")
        raise_err = False  # Don't actually raise

    sweep.event("Health sweep complete: 1 issue found")

# Pipeline 4: A failed one for variety
try:
    with agentwatch.trace("data-sync") as sync:
        sync.event("Starting data sync with external API")

        with agentwatch.trace("fetch-remote-data") as fetch:
            time.sleep(0.02)
            fetch.event("Fetched 1,200 records")

        with agentwatch.trace("transform-data") as transform:
            time.sleep(0.015)
            transform.event("Transformed 1,200 records")

        with agentwatch.trace("write-to-db") as write:
            time.sleep(0.01)
            raise ConnectionError("Database connection pool exhausted")
except ConnectionError:
    pass  # Expected — the trace captures the error

agentwatch.shutdown()

print("✅ Created 4 pipeline traces with nested spans")
print("   Run `agentwatch serve` and check the trace detail pages!\n")
