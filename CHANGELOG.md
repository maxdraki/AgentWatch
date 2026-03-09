# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-03-09

### Added

- **Core SDK** — `init()`, `shutdown()`, `get_agent()` for agent lifecycle
- **Tracing** — `trace()` context manager and decorator with nested span support, automatic error capture, thread-safe span stack
- **Logging** — `log()` with automatic trace context linking, level filtering
- **Health checks** — `health.register()` with flexible return types (bool/str/dict), duration tracking, last-run timestamps
- **Cost tracking** — `costs.record()` with built-in pricing for 30+ LLM models (OpenAI, Anthropic, Google, Meta, Mistral, DeepSeek), automatic cost estimation from token counts
- **Pattern detection** — `patterns.detect_patterns()` finds recurring errors, performance degradation, error spikes, slow traces
- **Alert system** — configurable rules with health, error rate, and cost threshold checks; webhook delivery; cooldown-based deduplication; custom alerts via `fire()`
- **Reports** — `reports.summary()` text report and `reports.summary_data()` structured data with traces, health, costs, errors, top failures
- **Data retention** — `retention.prune()` with per-type configurable retention, cascade deletes, dry-run mode; `retention.vacuum()`; `retention.export_jsonl()`
- **Configuration** — TOML/JSON config file support with env var overrides (`AGENTWATCH_*`), search hierarchy (project → user directory)
- **SQLite storage** — WAL mode, thread-safe, proper indexing, zero external dependencies
- **Web dashboard** — 8 pages (Overview, Traces, Trace Detail, Logs, Health, Costs, Patterns/Trends, Alerts, Agents Comparison), dark theme, mobile-responsive with hamburger nav
- **SVG chart system** — server-rendered sparklines, bar charts, donut charts (zero JavaScript dependencies)
- **Trace waterfall view** — nested spans with timing offsets, indentation, timeline ruler, grid lines
- **Auto-refresh health** — 30s polling with toggle, last-updated indicator
- **Trace search/filtering** — by name, duration range, time window, agent, status
- **Multi-agent comparison** — side-by-side table with traces, error rates, duration, health, cost, model breakdown
- **JSON API** — 16 endpoints covering stats, traces, logs, health, costs, patterns, trends, alerts, report, metrics
- **Prometheus/OpenMetrics exporter** — `/metrics` endpoint with traces, logs, health, costs, agent info metrics
- **CLI** — 16 commands: `status`, `traces`, `trace`, `logs`, `health`, `stats`, `costs`, `patterns`, `report`, `db info/prune/vacuum/export`, `serve`, `version`
- **OpenClaw integration** — `auto_instrument()` one-liner with session tracing, tool call spans, health checks, sensitive param filtering
- **Generic hooks** — `@traced()` decorator, `track_llm_call()`, `track_batch()`, `with_retry()`
- **FastAPI middleware** — `AgentWatchMiddleware` for automatic HTTP request tracing
- **Grafana dashboard template** — import-ready JSON with 12 panels
- **Dashboard authentication** — token-based auth with configurable API keys
- **Async tracing** — `async_trace()` for asyncio-based agents
- **PEP 561** — `py.typed` marker for type checker support

[Unreleased]: https://github.com/agentwatch/agentwatch/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/agentwatch/agentwatch/releases/tag/v0.1.0
