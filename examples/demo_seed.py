#!/usr/bin/env python3
"""
Seed the AgentWatch database with realistic demo data.

Run this to populate the dashboard with example traces, logs,
health checks, and cost records for a demo or development:

    python3 examples/demo_seed.py
    agentwatch serve
"""

import random
import sys
import os
import time

# Add src to path for direct execution
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import agentwatch
from agentwatch.costs import record as record_cost
from agentwatch.models import HealthStatus

# Initialise
agentwatch.init("demo-agent")

print("🌱 Seeding demo data...\n")

# ─── Traces ──────────────────────────────────────────────────────────────

TASKS = [
    ("process-emails", 0.05),      # 5% failure
    ("summarise-document", 0.10),  # 10% failure
    ("classify-ticket", 0.02),     # 2% failure
    ("generate-report", 0.15),     # 15% failure
    ("health-check-run", 0.01),    # 1% failure
    ("sync-calendar", 0.08),       # 8% failure
    ("send-notification", 0.03),   # 3% failure
]

print("  📊 Creating traces...")
for i in range(80):
    task_name, fail_rate = random.choice(TASKS)
    should_fail = random.random() < fail_rate

    with agentwatch.trace(task_name) as span:
        # Add some events
        span.event(f"Starting {task_name}")

        # Simulate work with variable duration
        time.sleep(random.uniform(0.001, 0.05))

        if task_name == "process-emails":
            count = random.randint(1, 15)
            span.event(f"Found {count} emails to process")
            span.set_metadata("email_count", count)

        if task_name == "summarise-document":
            words = random.randint(500, 5000)
            span.event(f"Document has {words} words")
            span.set_metadata("word_count", words)

        if should_fail:
            errors = [
                "API rate limit exceeded",
                "Connection timeout after 30s",
                "Authentication failed: token expired",
                "Invalid response format from upstream",
                "Database connection pool exhausted",
            ]
            span.set_error(random.choice(errors))
            raise_error = False  # We set error manually
        else:
            span.event(f"Completed successfully")

print(f"  ✅ Created 80 traces")

# ─── Logs ────────────────────────────────────────────────────────────────

print("  📝 Creating logs...")

LOG_MESSAGES = {
    "debug": [
        "Cache hit for key user:12345",
        "Retrying request (attempt 2/3)",
        "Token refresh scheduled in 15m",
    ],
    "info": [
        "Agent started successfully",
        "Processing batch of 12 items",
        "Calendar sync completed (3 events)",
        "Health checks all passing",
        "Notification delivered to 2 channels",
    ],
    "warn": [
        "API response time above threshold (2.3s)",
        "Memory usage at 78%",
        "Rate limit approaching (85/100)",
        "Stale cache entry detected, refreshing",
    ],
    "error": [
        "Failed to send notification: channel unreachable",
        "Database query timeout after 10s",
        "API returned 500: internal server error",
    ],
    "critical": [
        "All retry attempts exhausted for email-send",
        "Storage space critically low (95%)",
    ],
}

for level, messages in LOG_MESSAGES.items():
    count = {"debug": 15, "info": 25, "warn": 8, "error": 5, "critical": 2}[level]
    for _ in range(count):
        msg = random.choice(messages)
        agentwatch.log(level, msg, {"source": "demo"})

print(f"  ✅ Created {15+25+8+5+2} logs")

# ─── Health Checks ───────────────────────────────────────────────────────

print("  💚 Registering health checks...")

def check_database():
    return {"status": "ok", "message": "Connected, latency 3ms", "connections": 5}

def check_api():
    if random.random() < 0.1:
        return {"status": "warn", "message": "Elevated latency (450ms)"}
    return {"status": "ok", "message": "Healthy, latency 120ms"}

def check_storage():
    used_pct = random.randint(40, 75)
    status = "warn" if used_pct > 70 else "ok"
    return {"status": status, "message": f"{used_pct}% used", "used_pct": used_pct}

def check_memory():
    return True

agentwatch.health.register("database", check_database)
agentwatch.health.register("api-gateway", check_api)
agentwatch.health.register("storage", check_storage)
agentwatch.health.register("memory", check_memory)

# Run checks a few times for history
for _ in range(5):
    agentwatch.health.run_all()

print(f"  ✅ Created 4 health checks (5 runs each)")

# ─── Cost Records ────────────────────────────────────────────────────────

print("  💰 Creating cost records...")

MODELS = [
    ("claude-sonnet-4-20250514", 800, 2000, 200, 600),
    ("gpt-4o", 500, 1500, 100, 400),
    ("gpt-4o-mini", 1000, 3000, 200, 800),
    ("gemini-2.5-flash", 800, 2500, 300, 1000),
]

for _ in range(30):
    model, min_in, max_in, min_out, max_out = random.choice(MODELS)
    record_cost(
        model=model,
        input_tokens=random.randint(min_in, max_in),
        output_tokens=random.randint(min_out, max_out),
        metadata={"purpose": random.choice(["classification", "summarisation", "generation", "analysis"])},
    )

print(f"  ✅ Created 30 cost records")

# ─── Done ────────────────────────────────────────────────────────────────

agentwatch.shutdown()

print(f"\n🎉 Demo data seeded! Run the dashboard:\n")
print(f"    cd {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}")
print(f"    python3 -m agentwatch.server.app")
print(f"    # or: agentwatch serve")
print()
