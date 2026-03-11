"""
Remote agent example — sends traces to a central AgentWatch server.

This shows how an agent running on a separate machine can report
its traces, logs, health, and costs to a central dashboard.

Setup:
    1. Start the dashboard: agentwatch serve --port 8470
    2. Run this agent: python examples/remote_agent_example.py

The agent connects to the dashboard over HTTP and sends data
using the lightweight AgentWatchClient (no SDK init needed).
"""

import random
import time

from agentwatch.client import AgentWatchClient


def main():
    # Connect to the central dashboard
    client = AgentWatchClient(
        server_url="http://localhost:8470",
        agent_name="email-processor",
        # auth_token="your-token",  # If auth is enabled
        # buffer_size=10,           # Buffer and batch-send for efficiency
    )

    print(f"📡 Connected: {client}")
    print("Sending traces to http://localhost:8470\n")

    # Simulate an email processing agent
    for batch_num in range(1, 4):
        print(f"📧 Processing batch {batch_num}...")

        with client.trace(f"email-batch-{batch_num}") as t:
            t.set_metadata("batch_size", random.randint(5, 20))

            # Fetch emails
            with t.child("fetch-inbox") as fetch:
                time.sleep(random.uniform(0.1, 0.3))
                email_count = random.randint(3, 15)
                fetch.event(f"Found {email_count} unread emails")
                fetch.set_metadata("count", email_count)

            # Classify each email (with LLM)
            with t.child("classify-emails") as classify:
                for i in range(min(email_count, 5)):
                    with t.child(f"classify-email-{i}") as email:
                        time.sleep(random.uniform(0.05, 0.15))
                        category = random.choice(["urgent", "normal", "spam"])
                        email.set_metadata("category", category)

                        # Simulate LLM cost
                        client.cost(
                            model="gpt-4o-mini",
                            input_tokens=random.randint(200, 500),
                            output_tokens=random.randint(10, 50),
                            trace_id=t._trace_id,
                        )

                classify.event(f"Classified {min(email_count, 5)} emails")

            # Generate summary
            with t.child("generate-summary") as summary:
                time.sleep(random.uniform(0.1, 0.2))
                client.cost(
                    model="claude-sonnet-4-20250514",
                    input_tokens=random.randint(1000, 3000),
                    output_tokens=random.randint(200, 500),
                    trace_id=t._trace_id,
                )
                summary.event("Summary generated and sent")

            # Simulate occasional failures
            if random.random() < 0.2:
                t.set_error("SMTP connection timeout")
                client.log("error", "Failed to send summary email", {
                    "batch": batch_num,
                    "error": "SMTP timeout",
                })
            else:
                client.log("info", f"Batch {batch_num} completed successfully", {
                    "emails_processed": email_count,
                })

        # Send health check
        client.health(
            "smtp",
            status="ok" if random.random() > 0.1 else "warn",
            message="Connected" if random.random() > 0.1 else "High latency",
            duration_ms=random.uniform(5, 50),
        )

        print(f"  ✅ Batch {batch_num} sent")

    print(f"\n📊 Client stats: {client.stats}")
    print("🔭 View results at http://localhost:8470")


if __name__ == "__main__":
    main()
