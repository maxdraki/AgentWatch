"""
Example: Async AI Agent with AgentWatch observability.

Demonstrates how to instrument an asyncio-based agent with:
- Async tracing (context manager and decorator)
- Cost tracking per LLM call
- Health checks
- Structured logging
- Concurrent task tracing

Run with:
    python examples/async_agent_example.py
    agentwatch serve  # View results in dashboard
"""

import asyncio
import random
import time

import agentwatch
from agentwatch import async_trace


# ─── Simulated async operations ──────────────────────────────────────────

async def simulate_llm_call(prompt: str, model: str = "claude-sonnet-4-20250514") -> dict:
    """Simulate an async LLM API call."""
    await asyncio.sleep(random.uniform(0.1, 0.5))
    input_tokens = len(prompt.split()) * 2
    output_tokens = random.randint(50, 300)
    return {
        "content": f"Simulated response for: {prompt[:50]}",
        "model": model,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


async def simulate_db_query(query: str) -> list[dict]:
    """Simulate an async database query."""
    await asyncio.sleep(random.uniform(0.01, 0.1))
    return [{"id": i, "text": f"result-{i}"} for i in range(random.randint(0, 10))]


async def simulate_api_call(endpoint: str) -> dict:
    """Simulate an async external API call."""
    await asyncio.sleep(random.uniform(0.05, 0.3))
    if random.random() < 0.1:
        raise ConnectionError(f"Failed to reach {endpoint}")
    return {"status": "ok", "data": [1, 2, 3]}


# ─── Agent tasks ─────────────────────────────────────────────────────────

@async_trace("classify-ticket")
async def classify_ticket(ticket: dict) -> str:
    """Classify a support ticket using LLM."""
    response = await simulate_llm_call(
        f"Classify this ticket: {ticket['subject']}",
        model="claude-haiku-3.5",
    )
    agentwatch.costs.record(
        model=response["model"],
        input_tokens=response["usage"]["input_tokens"],
        output_tokens=response["usage"]["output_tokens"],
    )
    category = random.choice(["billing", "technical", "general", "urgent"])
    agentwatch.log("info", f"Classified ticket #{ticket['id']} as {category}")
    return category


@async_trace("generate-response")
async def generate_response(ticket: dict, category: str) -> str:
    """Generate a response for a classified ticket."""
    response = await simulate_llm_call(
        f"Write a {category} response for: {ticket['subject']}",
        model="claude-sonnet-4-20250514",
    )
    agentwatch.costs.record(
        model=response["model"],
        input_tokens=response["usage"]["input_tokens"],
        output_tokens=response["usage"]["output_tokens"],
    )
    return response["content"]


@async_trace("send-reply")
async def send_reply(ticket_id: int, response: str) -> bool:
    """Send the generated response."""
    await asyncio.sleep(random.uniform(0.05, 0.15))
    success = random.random() > 0.05
    if not success:
        agentwatch.log("error", f"Failed to send reply for ticket #{ticket_id}")
    return success


async def process_ticket(ticket: dict) -> None:
    """Process a single support ticket end-to-end."""
    async with async_trace("process-ticket", metadata={"ticket_id": ticket["id"]}) as span:
        span.event(f"Processing: {ticket['subject']}")

        # Classify
        category = await classify_ticket(ticket)
        span.set_metadata("category", category)

        # Look up customer context
        async with async_trace("lookup-context") as ctx_span:
            history = await simulate_db_query(f"SELECT * FROM tickets WHERE customer_id = {ticket['customer_id']}")
            ctx_span.set_metadata("history_count", len(history))

        # Generate response
        response = await generate_response(ticket, category)

        # Send reply
        sent = await send_reply(ticket["id"], response)
        span.set_metadata("sent", sent)
        span.event(f"Completed: {'sent' if sent else 'failed to send'}")


async def batch_process(tickets: list[dict]) -> None:
    """Process multiple tickets concurrently."""
    async with async_trace("batch-process", metadata={"count": len(tickets)}) as span:
        agentwatch.log("info", f"Starting batch of {len(tickets)} tickets")

        # Process in parallel (max 3 concurrent)
        semaphore = asyncio.Semaphore(3)

        async def limited_process(ticket):
            async with semaphore:
                await process_ticket(ticket)

        await asyncio.gather(
            *[limited_process(t) for t in tickets],
            return_exceptions=True,
        )

        agentwatch.log("info", f"Batch complete: {len(tickets)} tickets processed")


# ─── Health checks ───────────────────────────────────────────────────────

async def check_db():
    """Health check for database connectivity."""
    try:
        await simulate_db_query("SELECT 1")
        return True
    except Exception:
        return False


async def check_api():
    """Health check for external API."""
    try:
        await simulate_api_call("/health")
        return {"status": "ok", "latency_ms": random.randint(10, 100)}
    except Exception as e:
        return {"status": "critical", "error": str(e)}


# ─── Main ────────────────────────────────────────────────────────────────

async def main():
    """Run the async agent demo."""
    # Initialise AgentWatch
    agentwatch.init("ticket-agent")

    # Register health checks (sync wrappers for async checks)
    agentwatch.health.register("database", lambda: True)  # Simplified for demo
    agentwatch.health.register("api", lambda: True)
    agentwatch.health.register("queue", lambda: {"status": "ok", "depth": random.randint(0, 50)})

    agentwatch.log("info", "Ticket agent started")

    # Generate some fake tickets
    tickets = [
        {"id": i, "customer_id": random.randint(1, 20),
         "subject": random.choice([
             "Can't log in to my account",
             "Billing charge I don't recognise",
             "Feature request: dark mode",
             "App crashing on startup",
             "How do I export my data?",
             "Urgent: production down",
             "Password reset not working",
             "Invoice incorrect amount",
         ])}
        for i in range(1, 16)
    ]

    # Process in batches
    batch_size = 5
    for i in range(0, len(tickets), batch_size):
        batch = tickets[i:i + batch_size]
        await batch_process(batch)
        agentwatch.log("info", f"Completed batch {i // batch_size + 1}")

    # Run health checks
    results = agentwatch.health.run_all()
    for result in results:
        agentwatch.log("info", f"Health: {result.name} = {result.status.value}")

    # Generate report
    report = agentwatch.reports.summary()
    print("\n" + report)

    agentwatch.shutdown()
    print("\n✅ Done! Run 'agentwatch serve' to view the dashboard.")


if __name__ == "__main__":
    asyncio.run(main())
