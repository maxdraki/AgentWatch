# AgentWatch 🔍

**Observability for autonomous AI agents.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-251%20passing-brightgreen.svg)](tests/)
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
- Context manager and decorator API
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

**Dashboard**
- 8 pages: Overview, Traces, Trace Detail, Logs, Health, Costs, Patterns, Agents
- Trace search and filtering (by name, agent, status, duration, time window)
- Multi-agent comparison with side-by-side stats
- SVG charts — zero JavaScript dependencies
- Dark theme, mobile-friendly

**Production Ready**
- Prometheus `/metrics` endpoint — drop-in for Grafana
- Grafana dashboard template (12 panels, import-ready JSON)
- JSONL export for BigQuery, pandas, backup
- Data retention with per-type day limits
- FastAPI auto-instrumentation middleware
- Config files (`agentwatch.toml`) with env var overrides

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

### 4. Open the dashboard

```bash
agentwatch serve
```

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

### Auto-instrument FastAPI

```python
from fastapi import FastAPI
from agentwatch.integrations.fastapi import AgentWatchMiddleware

app = FastAPI()
app.add_middleware(AgentWatchMiddleware)

# Every request is now traced automatically
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
agentwatch logs                # Recent logs (--level, --hours)
agentwatch health              # Run all health checks
agentwatch costs               # Cost summary by model
agentwatch patterns            # Detected issues and trends
agentwatch report              # Full status report (--hours)
agentwatch serve               # Start dashboard (--port, --host, --metrics)

agentwatch db info             # Database stats
agentwatch db prune            # Remove old data (--days, --dry-run)
agentwatch db vacuum           # Reclaim disk space
agentwatch db export           # Export to JSONL (-o file, --agent, --hours)

# All commands support --json for machine-readable output
```

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
├── retention.py     # prune(), vacuum(), db_info()
├── config.py        # TOML/JSON/env config loading
├── server/          # FastAPI dashboard and API
├── cli/             # Click CLI commands
├── exporters/       # Prometheus exporter, JSONL export
└── integrations/    # OpenClaw, FastAPI, generic hooks
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

Issues, PRs and ideas welcome. The codebase is intentionally clean — 251 tests, typed throughout, zero magic.

Areas most likely to benefit from contributions:
- Additional LLM provider integrations
- Alert delivery channels (Slack, Telegram, email)
- Cloud sync / hosted dashboard
- Real-world usage examples

---

## License

MIT — do what you like with it.
