#!/usr/bin/env python3
"""
Full AgentWatch example — a realistic autonomous agent workflow.

This demonstrates:
- Session tracing with nested spans
- Tool call tracking
- Cost tracking with automatic model pricing
- Health checks
- Pattern detection
- Structured logging

Run:
    python3 examples/full_agent_example.py
    agentwatch serve
"""

import os
import sys
import time
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import agentwatch
from agentwatch.integrations.openclaw import auto_instrument
from agentwatch.integrations.hooks import traced, track_batch

# ─── Setup ───────────────────────────────────────────────────────────────

print("🔍 AgentWatch Full Example\n")

inst = auto_instrument(
    "email-agent",
    track_costs=True,
    track_health=True,
    auto_detect_name=False,
)

# Register custom health checks
inst.register_health_check("email-server", lambda: {
    "status": "ok",
    "message": "IMAP connected, latency 45ms",
    "latency_ms": 45,
})

inst.register_health_check("classifier-model", lambda: {
    "status": "ok" if random.random() > 0.1 else "warn",
    "message": "Model loaded, 98.5% accuracy",
    "accuracy": 0.985,
})

# ─── Simulate agent runs ────────────────────────────────────────────────

CATEGORIES = ["support", "billing", "feature-request", "spam", "urgent"]
SUBJECTS = [
    "Can't login to my account",
    "Invoice #4521 question",
    "Feature suggestion: dark mode",
    "Congratulations! You've won!",
    "URGENT: Production down",
    "How to export data?",
    "Subscription cancellation",
    "API rate limiting issue",
    "Meeting follow-up notes",
    "Bug report: mobile app crash",
]

print("📧 Running email processing simulation...\n")

for run in range(5):
    # Each run simulates one agent cycle (e.g., cron trigger)
    with inst.session(f"email-check-{run}") as session:
        session.event(f"Cycle {run + 1}: checking for new emails")

        # Step 1: Fetch emails
        with inst.tool_call("imap_fetch", {"folder": "INBOX"}) as fetch:
            time.sleep(0.01)
            email_count = random.randint(0, 8)
            fetch.event(f"Found {email_count} new emails")
            fetch.set_metadata("email_count", email_count)

        if email_count == 0:
            session.event("No new emails, skipping")
            continue

        # Step 2: Classify each email
        emails = [{"subject": random.choice(SUBJECTS), "id": i} for i in range(email_count)]

        def classify_email(email):
            time.sleep(0.005)
            if random.random() < 0.05:
                raise ValueError("Classification timeout")
            return random.choice(CATEGORIES)

        results = track_batch(
            f"classify-batch-{run}",
            emails,
            classify_email,
        )

        # Step 3: Track LLM costs for classification
        inst.record_model_usage(
            model="claude-sonnet-4-20250514",
            input_tokens=random.randint(800, 2000),
            output_tokens=random.randint(100, 400),
            metadata={"purpose": "email-classification", "batch_size": email_count},
        )

        # Step 4: Process urgent emails
        urgent = [r for r in results if r.get("result") == "urgent"]
        if urgent:
            with inst.tool_call("send_alert", {"channel": "slack"}) as alert:
                time.sleep(0.008)
                alert.event(f"Sent alert for {len(urgent)} urgent emails")

        # Step 5: Generate summaries
        if email_count > 3:
            with inst.tool_call("generate_summary") as summary:
                time.sleep(0.02)
                inst.record_model_usage(
                    model="gpt-4o-mini",
                    input_tokens=random.randint(500, 1500),
                    output_tokens=random.randint(200, 600),
                    metadata={"purpose": "email-summary"},
                )
                summary.event(f"Generated digest for {email_count} emails")

        session.event(f"Cycle complete: {email_count} emails processed")

    # Log completion
    inst.log("info", f"Email check cycle {run + 1} complete", {
        "emails": email_count,
        "classified": len([r for r in results if r["error"] is None]),
        "errors": len([r for r in results if r["error"] is not None]),
    })

# ─── Run health checks ──────────────────────────────────────────────────

print("💚 Running health checks...")
results = inst.run_health_checks()
for check in results:
    print(f"   {check.status.value:>8} │ {check.name}: {check.message}")

# ─── Pattern detection ───────────────────────────────────────────────────

print("\n🔍 Detecting patterns...")
patterns = agentwatch.patterns.detect_patterns(window_hours=1)
if patterns:
    for p in patterns:
        print(f"   [{p.severity.value}] {p.title}")
else:
    print("   ✅ No patterns detected")

trend = agentwatch.patterns.detect_trends(window_hours=1)
print(f"\n📈 System trend: {trend.direction.value}")
if trend.summary:
    print(f"   {trend.summary}")

# ─── Summary ─────────────────────────────────────────────────────────────

cost_summary = agentwatch.costs.summary()
print(f"\n💰 Total cost: ${cost_summary.total_cost_usd:.4f}")
print(f"   Tokens: {cost_summary.total_input_tokens:,} in / {cost_summary.total_output_tokens:,} out")

# ─── Cleanup ─────────────────────────────────────────────────────────────

inst.stop()

print(f"\n✅ Done! Run the dashboard:")
print(f"   agentwatch serve")
print(f"   # → http://localhost:8470\n")
