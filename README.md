# AgentWatch 🔍

**Observability for autonomous AI agents.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-471%20passing-brightgreen.svg)](tests/)
[![Zero dependencies](https://img.shields.io/badge/core%20deps-zero-orange.svg)](pyproject.toml)

Most observability tools assume a human is watching. AgentWatch is built for agents that **run themselves**.

```python
import agentwatch

agentwatch.init("my-agent")

with agentwatch.trace("process-emails") as span:
    span.event("found 12 unread")
    results = classify_emails(inbox)
    span.set_metadata("processed", len(results))
```

```bash
agentwatch serve
# → Dashboard at http://localhost:8470
```

---

## Why AgentWatch?

Autonomous agents — cron jobs, overnight build sessions, always-on assistants — have an observability problem. They run when you're not watching. They fail silently. They rack up API costs with no visibility. And most existing tools (Datadog, Langfuse, LangSmith) either assume you're there, require cloud signup, or cost money before you've shipped anything.

AgentWatch is local-first, zero-dependency, and built specifically for agents that operate autonomously.

| | AgentWatch | LangSmith | Datadog | print() |
|---|---|---|---|---|
| Setup time | 3 lines | Cloud account + SDK | Agent + API key | 0 |
| Cost tracking | ✅ Built-in | ✅ | ❌ | ❌ |
| Pattern detection | ✅ | ❌ | Partial | ❌ |
| Local-first | ✅ | ❌ | ❌ | ✅ |
| Works offline | ✅ | ❌ | ❌ | ✅ |
| Core dependencies | **0** | Many | Agent | 0 |
| Price | Free | Paid tiers | $$$ | Free |

---

## Features

**Tracing**
- Nested spans with automatic parent detection via thread-local context
- Context manager and decorator API — sync and async (`async_trace()`)
- Async support using `contextvars` for proper coroutine context propagation
- Per-span events, metadata, timing
- Waterfall visualization in the dashboard

**Health Checks**
- Register any function as a health check
- Returns bool, string, or rich dict — flexible
- Auto-refresh dashboard with live status
- Configurable thresholds

**Cost Tracking**
- Built-in pricing for 25+ models (Claude, GPT-4.1, O3/O4, Gemini, Llama 4, DeepSeek, Mistral)
- Per-trace cost attribution
- Daily/weekly cost trends
- Custom pricing for any model

**Pattern Detection**
- Recurring errors (same error type appearing repeatedly)
- Performance degradation (response times trending up)
- Error spikes (sudden failure rate increase)
- Slow trace detection (statistical outliers)

**Custom Metrics**
- Record gauges and counters: `agentwatch.metric("queue_depth", 42)`
- Tag-based filtering and grouping
- Summary statistics (min, max, avg, count, series)
- Auto-linked to active trace context
- Prometheus export of custom metrics

**Dashboard**
- 9 pages: Overview, Traces, Trace Detail, Logs, Health, Costs, Metrics, Patterns, Agents
- Full search and filtering on traces and logs (by name, agent, status, level, time window)
- Multi-agent comparison with side-by-side stats
- SVG sparklines on metrics dashboard
- Dark theme, mobile-friendly

**Dashboard Authentication**
- Token-based auth — query param, Bearer header, cookie, or custom header
- Styled login page matching dashboard theme
- Excluded paths (health, metrics) for monitoring access
- `agentwatch serve --auth-token TOKEN` or `AGENTWATCH_AUTH_TOKEN` env var

**Production Ready**
- Prometheus `/metrics` endpoint — drop-in for Grafana
- Grafana dashboard template (12 panels, import-ready JSON)
- JSONL export for BigQuery, pandas, backup
- Data retention with per-type day limits
- FastAPI auto-instrumentation middleware
- Config files (`agentwatch.toml`) with env var overrides
- Live log tailing (`agentwatch tail`)

---

## Install

```bash
pip install agentwatch

# With dashboard + server
pip install agentwatch[server]

# Full install (dev tools)
pip install agentwatch[server,dev]
```

---

## Quick Start

### 1. Trace your agent

```python
import agentwatch

agentwatch.init("my-agent")

# Context manager — traces a block of work
with agentwatch.trace("classify-ticket") as span:
    span.event("processing ticket #1234")
    result = classify(ticket)
    span.set_metadata("category", result.category)
    span.set_metadata("confidence", result.score)

# Decorator — traces a function automatically
@agentwatch.trace("send-notification")
def notify(user_id: str, message: str):
    ...

# Nested traces — parent detected automatically
with agentwatch.trace("daily-pipeline"):
    with agentwatch.trace("fetch-data"):
        data = fetch()
    with agentwatch.trace("process"):
        output = process(data)
    with agentwatch.trace("report"):
        send_report(output)
```

### 2. Track costs

```python
agentwatch.costs.record(
    model="claude-sonnet-4-20250514",
    input_tokens=2500,
    output_tokens=800,
)

# Aliases work — "sonnet", "gpt4", "haiku", "flash", etc.
agentwatch.costs.record(model="sonnet", input_tokens=500, output_tokens=200)

# Inside a trace for automatic attribution
with agentwatch.trace("llm-call") as span:
    response = client.messages.create(...)
    agentwatch.costs.record(
        model="claude-sonnet-4-20250514",
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
```

### 3. Register health checks

```python
agentwatch.health.register("database", lambda: db.ping())
agentwatch.health.register("api", check_upstream)
agentwatch.health.register("disk", lambda: {"status": "ok", "free_gb": get_free_gb()})

# Returns bool, string, or dict — all valid
agentwatch.health.register("queue", lambda: queue_depth() < 1000)
agentwatch.health.register("cache", lambda: "degraded" if cache_miss_rate() > 0.5 else "ok")
```

### 4. Track custom metrics

```python
# Record a gauge (point-in-time value)
agentwatch.metric("queue_depth", 42)

# Record with tags for filtering
agentwatch.metric("requests", 1, tags={"method": "POST", "status": "200"})

# Record a counter
agentwatch.metric("errors_total", 5, kind="counter")

# Query metrics programmatically
from agentwatch.metrics import query, summary
points = query("queue_depth", hours=24)
stats = summary("queue_depth", hours=1)
```

### 5. Open the dashboard

```bash
agentwatch serve
```

---

## Async Tracing

For asyncio-based agents, use `async_trace()` — it uses `contextvars` for proper async context propagation:

```python
import agentwatch
from agentwatch import async_trace

agentwatch.init("my-async-agent")

# Async context manager
async with async_trace("fetch-data") as span:
    span.event("calling upstream API")
    data = await client.get("/data")

# Async decorator
@async_trace("process-batch")
async def process_batch(items: list):
    results = await asyncio.gather(*[process(item) for item in items])
    return results

# Nested async traces — parent detected via contextvars
async with async_trace("pipeline"):
    async with async_trace("step-1"):
        data = await fetch()
    async with async_trace("step-2"):
        result = await transform(data)

# Bare decorator (uses function name)
@async_trace
async def my_task():
    await do_work()
```

Async traces create the same spans and persist to the same storage as sync traces — dashboard, CLI, and API work identically.

---

## Structured Logging

```python
agentwatch.log("info", "Agent started")
agentwatch.log("warn", "API latency above threshold", {"latency_ms": 2300})
agentwatch.log("error", "Payment failed", {"user_id": "u_123", "amount": 49.99})

# Logs are automatically linked to the active trace context
with agentwatch.trace("checkout"):
    agentwatch.log("info", "Processing payment")  # Linked to "checkout" trace
    charge(user)
```

---

## Pattern Detection

```python
# Detect what's going wrong (last 24h by default)
patterns = agentwatch.patterns.detect_patterns(window_hours=24)

for pattern in patterns:
    print(f"[{pattern.severity}] {pattern.title}")
    print(f"  {pattern.description}")

# Get trend direction
trend = agentwatch.patterns.detect_trends()
print(f"System is {trend.direction}")  # "improving", "stable", "degrading"
```

```bash
# Via CLI
agentwatch patterns
agentwatch patterns --json
```

---

## Prometheus & Grafana

AgentWatch exposes `/metrics` in OpenMetrics format, ready for any Prometheus-compatible scraper.

```bash
agentwatch serve --metrics  # or set metrics=true in agentwatch.toml
```

**Metrics exposed:**

```
agentwatch_traces_total{agent="my-agent", status="ok"} 1247
agentwatch_trace_duration_seconds_avg{agent="my-agent"} 2.34
agentwatch_error_rate_pct{agent="my-agent"} 1.2
agentwatch_logs_total{agent="my-agent", level="error"} 3
agentwatch_health_status{agent="my-agent", check="database"} 1
agentwatch_tokens_total{agent="my-agent", model="claude-sonnet", direction="input"} 1250000
agentwatch_cost_usd_total{agent="my-agent", model="claude-sonnet"} 3.45
agentwatch_agents_active 2
```

**prometheus.yml:**
```yaml
scrape_configs:
  - job_name: agentwatch
    static_configs:
      - targets: ['localhost:8470']
    scrape_interval: 30s
```

A Grafana dashboard template (12 panels) is included at `examples/grafana_dashboard.json`. Import it directly into Grafana.

---

## Remote Agents (HTTP Client)

Run agents on any machine and send traces to a central AgentWatch dashboard:

```python
from agentwatch import AgentWatchClient

client = AgentWatchClient(
    server_url="http://dashboard-host:8470",
    agent_name="my-remote-agent",
    auth_token="secret",  # optional
)

# Trace with nested spans
with client.trace("process-batch") as t:
    t.event("started processing")
    with t.child("fetch-data") as fetch:
        data = fetch_from_api()
        fetch.set_metadata("count", len(data))
    with t.child("transform") as transform:
        results = process(data)

# Logs, health, and costs
client.log("info", "Agent started", {"version": "1.0"})
client.health("database", status="ok", message="Connected")
client.cost(model="gpt-4o", input_tokens=500, output_tokens=200)
```

The client uses only stdlib (`urllib`) — no extra dependencies. For high-throughput agents, enable buffering:

```python
client = AgentWatchClient(
    server_url="http://host:8470",
    agent_name="high-volume-agent",
    buffer_size=50,  # Batch-send every 50 records
)
# ... send data ...
client.flush()  # Send any remaining buffered records
```

### Ingestion API

The server accepts data via REST endpoints (also usable from non-Python agents):

```bash
# Single trace
curl -X POST http://localhost:8470/api/v1/ingest/traces \
  -H "Content-Type: application/json" \
  -d '{"name": "my-task", "agent_name": "curl-agent", "status": "completed", "duration_ms": 1500}'

# Batch of mixed records
curl -X POST http://localhost:8470/api/v1/ingest/batch \
  -H "Content-Type: application/json" \
  -d '{"traces": [...], "logs": [...], "health": [...], "costs": [...]}'
```

Endpoints: `/api/v1/ingest/traces`, `/api/v1/ingest/logs`, `/api/v1/ingest/health`, `/api/v1/ingest/costs`, `/api/v1/ingest/batch`.

---

## Docker

Run the dashboard in Docker with persistent storage:

```bash
# Quick start
docker run -p 8470:8470 -v agentwatch-data:/data agentwatch

# With authentication
docker run -p 8470:8470 -v agentwatch-data:/data \
  -e AGENTWATCH_AUTH_TOKEN=my-secret agentwatch

# Using docker-compose
docker compose up -d
```

Build from source:

```bash
docker build -t agentwatch .
```

The image includes a health check and runs as a non-root user.

---

## Multi-Agent Support

Run multiple agents, each writing to the same DB — AgentWatch tracks them separately.

```python
# Agent 1
agentwatch.init("email-processor")

# Agent 2 (different process)
agentwatch.init("report-generator")
```

The `/agents` dashboard page shows a side-by-side comparison: traces, error rates, costs, health status, and model usage per agent.

---

## Integrations

### Auto-instrument an OpenClaw agent

```python
from agentwatch.integrations.openclaw import auto_instrument

inst = auto_instrument("my-openclaw-agent")

with inst.session("handle-message") as span:
    with inst.tool_call("web_search", {"query": "weather"}) as tool:
        results = search("weather")
        tool.event(f"got {len(results)} results")

    inst.record_model_usage(
        model="claude-sonnet-4-20250514",
        input_tokens=500,
        output_tokens=200,
    )
```

### Auto-instrument LangChain

```python
from agentwatch.integrations.langchain import AgentWatchHandler

handler = AgentWatchHandler()

# Pass to any LangChain component
llm = ChatOpenAI(callbacks=[handler])
chain = prompt | llm
result = chain.invoke({"input": "hello"}, config={"callbacks": [handler]})

# Or install globally
from agentwatch.integrations.langchain import auto_instrument
auto_instrument()  # All LLM/chain/tool/retriever calls are now traced

# Check stats
print(handler.stats)  # {"calls": 5, "total_tokens": 12000, "total_cost_usd": 0.23}
```

The handler captures LLM calls, chain runs, tool invocations, and retriever queries with full token usage and cost tracking.

### Auto-instrument CrewAI

```python
from crewai import Agent, Task, Crew
from agentwatch.integrations.crewai import instrument_crew

crew = Crew(agents=[...], tasks=[...])
crew = instrument_crew(crew, crew_name="research-crew")
result = crew.kickoff()  # Automatically traced

# Or use callbacks directly for more control
from agentwatch.integrations.crewai import AgentWatchCrewCallbacks

callbacks = AgentWatchCrewCallbacks()
crew = Crew(
    agents=[...],
    tasks=[...],
    step_callback=callbacks.on_step,
    task_callback=callbacks.on_task_complete,
)

with callbacks.trace_crew("my-crew"):
    result = crew.kickoff()
```

### Auto-instrument FastAPI

```python
from fastapi import FastAPI
from agentwatch.integrations.fastapi import AgentWatchMiddleware

app = FastAPI()
app.add_middleware(AgentWatchMiddleware)

# Every request is now traced automatically
```

### Auto-instrument LiteLLM

```python
import litellm
from agentwatch.integrations.litellm import auto_instrument

auto_instrument()

# All LiteLLM calls are now automatically traced with costs
response = litellm.completion(
    model="claude-sonnet-4-20250514",
    messages=[{"role": "user", "content": "Hello"}],
)
```

Or use the callback directly for more control:

```python
from agentwatch.integrations.litellm import AgentWatchCallback

callback = AgentWatchCallback(
    capture_messages=True,  # Log input/output (default: False for privacy)
)
litellm.callbacks = [callback]

# Check stats
print(callback.stats)  # {"calls": 10, "total_tokens": 5000, ...}
```

### Generic function tracing

```python
from agentwatch.integrations.hooks import traced, track_llm_call, track_batch, with_retry

# Decorator
@traced("classify")
def classify_email(email: dict) -> str:
    ...

# Auto-extract token usage from Anthropic/OpenAI responses
result = track_llm_call(
    fn=lambda: client.messages.create(model="claude-sonnet-4-20250514", ...),
    model="claude-sonnet-4-20250514",
)

# Per-item tracing for batch jobs
results = track_batch("process-batch", items, process_one)

# Trace each retry attempt
result = with_retry("flaky-api-call", call_api, max_retries=3)
```

---

## OpenTelemetry Export (OTLP)

Forward AgentWatch traces to any OpenTelemetry-compatible backend (Jaeger, Grafana Tempo, Honeycomb, Datadog, etc.):

```python
from agentwatch.exporters.otlp import OTLPExporter
from agentwatch.storage import Storage

storage = Storage()
exporter = OTLPExporter(
    endpoint="http://localhost:4318/v1/traces",
    service_name="my-agent",
    headers={"Authorization": "Bearer api-key"},  # Optional
)

# Export recent traces
exported = exporter.export_recent(storage, hours=1)
print(f"Exported {exported} traces")

# Or run as a background thread (auto-exports new traces)
exporter.start_background(storage, interval_seconds=30)
# ... agent runs ...
exporter.stop_background()
```

No dependency on the OpenTelemetry SDK — AgentWatch builds OTLP/HTTP JSON payloads directly using stdlib.

---

## Database Management

```bash
# Show DB stats (size, row counts per table)
agentwatch db info

# Preview what would be pruned
agentwatch db prune --days 30 --dry-run

# Prune and reclaim space
agentwatch db prune --days 30
agentwatch db vacuum

# Export to JSONL (for analysis, backup, migration)
agentwatch db export -o backup.jsonl
agentwatch db export --agent my-agent --hours 24 -o recent.jsonl
```

```python
# Programmatic retention
agentwatch.retention.prune(
    trace_days=30,
    log_days=7,
    health_days=14,
    cost_days=90,
    dry_run=True,  # Preview first
)

info = agentwatch.retention.db_info()
print(f"{info.size_mb:.1f} MB · {info.table_counts['traces']} traces")
```

---

## Configuration

```toml
# agentwatch.toml (project or user directory)

[agent]
name = "my-agent"

[server]
host = "0.0.0.0"
port = 8470
metrics = true

[retention]
trace_days = 30
log_days = 7
health_days = 14
cost_days = 90

[costs.pricing]
"my-fine-tuned-model" = [2.0, 8.0]  # [input $/1M, output $/1M]
```

Environment variables override file config:

```bash
AGENTWATCH_NAME=my-agent
AGENTWATCH_PORT=9000
AGENTWATCH_METRICS=true
```

---

## CLI Reference

```bash
agentwatch status              # Agent stats overview
agentwatch traces              # Recent traces (--search, --hours, --min-duration)
agentwatch trace <id>          # Trace detail with spans
agentwatch logs                # Recent logs (--level, --search, --hours)
agentwatch health              # Run all health checks
agentwatch costs               # Cost summary by model
agentwatch metrics             # Custom metrics (--name, --agent)
agentwatch patterns            # Detected issues and trends
agentwatch report              # Full status report (--hours)
agentwatch tail                # Follow logs in real-time (--traces, --level)
agentwatch serve               # Start dashboard (--port, --host, --auth-token)

agentwatch db info             # Database stats
agentwatch db prune            # Remove old data (--days, --dry-run)
agentwatch db vacuum           # Reclaim disk space
agentwatch db export           # Export to JSONL (-o file, --agent, --hours)

# All commands support --json for machine-readable output
```

### Dashboard Authentication

Protect your dashboard with a token:

```bash
# Via CLI flag
agentwatch serve --auth-token "my-secret-token"

# Via environment variable
export AGENTWATCH_AUTH_TOKEN="my-secret-token"
agentwatch serve
```

When auth is enabled:
- Dashboard pages redirect to a login screen
- API endpoints return `401` without a valid token
- Tokens accepted via: Bearer header, `X-AgentWatch-Token` header, `?token=` query param, or login cookie
- `/metrics` and `/health` are excluded (so Prometheus can scrape without auth)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Your Agent                               │
│   agentwatch.trace()  ·  agentwatch.log()  ·  costs.record()   │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │    AgentWatch SDK     │
                    │   (zero deps, stdlib) │
                    └───────────┬───────────┘
                                │
                    ┌───────────▼───────────┐
                    │    SQLite Storage     │
                    │  ~/.agentwatch/*.db   │
                    │  (WAL mode, indexed)  │
                    └──────┬────────────────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
┌─────────▼──────┐ ┌───────▼──────┐ ┌──────▼───────┐
│   Dashboard    │ │  Prometheus  │ │  JSONL/CLI   │
│  (FastAPI)     │ │  /metrics    │ │  Export      │
│  Port 8470     │ │  Grafana     │ │  Analysis    │
└────────────────┘ └──────────────┘ └──────────────┘
```

**Core SDK** — pure Python stdlib, no external dependencies.  
**Storage** — SQLite with WAL mode, thread-safe, works across processes.  
**Dashboard** — FastAPI + Jinja2 (optional install), SVG charts, no JS frameworks.  
**CLI** — Click-based, ships with the core package.

---

## Development

```bash
git clone https://github.com/maxdraki/AgentWatch.git
cd AgentWatch

pip install -e ".[server,dev]"

# Run tests
pytest

# Seed demo data and explore the dashboard
python examples/demo_seed.py
agentwatch serve
```

### Project structure

```
src/agentwatch/
├── core.py          # init(), global state
├── tracing.py       # trace() context manager/decorator
├── logging.py       # log()
├── health.py        # health.register(), health.run_all()
├── costs.py         # costs.record(), pricing table
├── storage.py       # SQLite backend
├── models.py        # Dataclasses: Trace, Span, Log, HealthResult
├── patterns.py      # Pattern and trend detection
├── alerts.py        # Alert rules and webhooks
├── reports.py       # summary() and summary_data()
├── async_tracing.py # Async trace support (contextvars)
├── auth.py          # Dashboard authentication
├── retention.py     # prune(), vacuum(), db_info()
├── metrics.py       # Custom metrics (gauge/counter)
├── config.py        # TOML/JSON/env config loading
├── server/          # FastAPI dashboard and API
├── cli/             # CLI commands (argparse)
├── exporters/       # Prometheus, OTLP, JSONL
└── integrations/    # LangChain, CrewAI, FastAPI, LiteLLM, OpenClaw
```

---

## Model Pricing

Built-in pricing (as of March 2026) for:

**Anthropic** — Claude Sonnet 4, Haiku 3.5, Opus 4, and dated variants  
**OpenAI** — GPT-4.1, GPT-4.1 Mini/Nano, O3, O4-Mini, GPT-4o, GPT-4o Mini  
**Google** — Gemini 2.0 Flash, Gemini 2.0 Flash Lite, Gemini 1.5 Pro/Flash  
**Meta** — Llama 4 Maverick, Llama 4 Scout, Llama 3.3 70B  
**DeepSeek** — DeepSeek V3, DeepSeek R1  
**Mistral** — Mistral Large, Mistral Small  

Add custom pricing in config or at runtime:

```python
agentwatch.costs.set_pricing("my-model", input_per_mtok=3.0, output_per_mtok=9.0)
```

---

## Contributing

Issues, PRs and ideas welcome. The codebase is intentionally clean — 466 tests, typed throughout, zero magic.

Areas most likely to benefit from contributions:
- Additional LLM provider integrations
- Alert delivery channels (Slack, Telegram, email)
- Cloud sync / hosted dashboard
- Real-world usage examples

---

## License

MIT — do what you like with it.
